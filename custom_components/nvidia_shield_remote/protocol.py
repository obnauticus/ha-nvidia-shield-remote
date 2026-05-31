"""NVIDIA Shield remote protocol client.

This implements the Shield app protocol observed in NVIDIA's Android app:
TLS on TCP 8987 with binary protobuf-like messages. The payloads below are
kept as hex strings so they can be checked directly against packet captures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import asyncio
import os
import socket
import ssl
import tempfile
import threading
from typing import Iterable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from cryptography.x509.oid import NameOID

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 8.0
PAIRING_READ_TIMEOUT = 20.0

LOGIN_REQUEST = "0801121a0801121073616d73756e6720534d2d4739393855180128fbff04"
PAIRING_REQUEST = "080a120308cd08"
LOGIN_SUCCESS_PREFIX = bytes.fromhex("08f007")
PAIRING_CERT_PREFIX = bytes.fromhex("08b510")
SUCCESS_TEXT = b"Success"

KEY_PAYLOADS: dict[str, tuple[str, ...]] = {
    "UP": (
        "08e907120c08141001200a28013202ce01",
        "08e907120c08141001200a28023202ce01",
    ),
    "DOWN": (
        "08e907120c08141001200a28013202d801",
        "08e907120c08141001200a28023202d801",
    ),
    "RIGHT": (
        "08e907120c08141001200a28013202d401",
        "08e907120c08141001200a28023202d401",
    ),
    "LEFT": (
        "08e907120c08141001200a28013202d201",
        "08e907120c08141001200a28023202d201",
    ),
    "SELECT": (
        "08e907120c08141001200a28013202c205",
        "08e907120c08141001200a28023202c205",
    ),
    "ENTER": (
        "08e907120c08141001200a28013202c205",
        "08e907120c08141001200a28023202c205",
    ),
    "HOME": (
        "08e907120c08141001200a28013202d802",
        "08e907120c08141001200a28023202d802",
    ),
    "BACK": (
        "08e907120c08141001200a28013202bc02",
        "08e907120c08141001200a28023202bc02",
    ),
    "MENU": (
        "08e907120c08141001200a280132029602",
        "08e907120c08141001200a280232029602",
    ),
    "PLAY_PAUSE": (
        "08e907120c08141001200a28013202f604",
        "08e907120c08141001200a28023202f604",
    ),
    "REWIND": (
        "08e907120c08141001200a28013202d002",
        "08e907120c08141001200a28023202d002",
    ),
    "FAST_FORWARD": (
        "08e907120c08141001200a28013202a003",
        "08e907120c08141001200a28023202a003",
    ),
    "POWER": ("08e907120808141005201e401e",),
    "POWERON": ("08e907120808141005201e4010",),
    "WAKE": ("08e907120808141005201e4010",),
    "VOLUME_UP": ("08f007120c08031208080110031a020102",),
    "VOLUME_DOWN": ("08f007120c08031208080110011a020102",),
    "MUTE": ("08f007120c08031208080110021a020102",),
}


class ShieldProtocolError(Exception):
    """Base class for Shield protocol errors."""


class ShieldNotPairedError(ShieldProtocolError):
    """Raised when a paired command is requested before pairing."""


class ShieldConnectionError(ShieldProtocolError):
    """Raised when the Shield cannot be reached."""


@dataclass(slots=True)
class ShieldCredentials:
    """Stored Shield TLS client credentials."""

    cert_pem: str
    key_pem: str


@dataclass(slots=True)
class PairingResult:
    """Credentials returned by the Shield after PIN pairing."""

    credentials: ShieldCredentials


@dataclass(slots=True)
class _PairingSession:
    socket: ssl.SSLSocket

    def close(self) -> None:
        """Close the pairing socket."""
        try:
            self.socket.close()
        except OSError:
            pass


class ShieldProtocolClient:
    """Blocking protocol client wrapped by async methods."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        credentials: ShieldCredentials | None = None,
    ) -> None:
        """Initialize the Shield client."""
        self.host = host
        self.port = port
        self.credentials = credentials
        self.assumed_on: bool | None = None
        self.last_available = True
        self._lock = asyncio.Lock()
        self._thread_lock = threading.RLock()
        self._pairing_session: _PairingSession | None = None

    @property
    def paired(self) -> bool:
        """Return whether the client has stored credentials."""
        return self.credentials is not None

    def update_credentials(self, credentials: ShieldCredentials) -> None:
        """Update credentials after a successful pairing flow."""
        self.credentials = credentials

    async def async_ping(self) -> bool:
        """Check whether the Shield remote port accepts TCP connections."""
        return await asyncio.to_thread(self._ping)

    async def async_request_pairing(self) -> None:
        """Request an on-screen PIN from the Shield."""
        async with self._lock:
            await asyncio.to_thread(self._request_pairing)

    async def async_submit_pin(self, pin: str) -> PairingResult:
        """Submit an on-screen PIN and return credentials."""
        async with self._lock:
            return await asyncio.to_thread(self._submit_pin, pin)

    async def async_send_key(self, key: str) -> None:
        """Send a single command key."""
        async with self._lock:
            await asyncio.to_thread(self._send_key, key)

    async def async_send_keys(self, keys: Iterable[str]) -> None:
        """Send multiple command keys."""
        async with self._lock:
            await asyncio.to_thread(self._send_keys, list(keys))

    async def async_wake(self) -> None:
        """Wake the Shield using NVIDIA's non-toggle power-on command."""
        await self.async_send_key("POWERON")
        self.assumed_on = True

    async def async_sleep(self) -> None:
        """Put the Shield into standby using the Shield power command."""
        await self.async_send_key("POWER")
        self.assumed_on = False

    async def async_power_toggle(self) -> None:
        """Toggle Shield power state."""
        await self.async_send_key("POWER")
        self.assumed_on = None

    def close(self) -> None:
        """Close any pending pairing state."""
        with self._thread_lock:
            self._close_pairing_session()

    def _ping(self) -> bool:
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=CONNECT_TIMEOUT
            ):
                self.last_available = True
                return True
        except OSError:
            self.last_available = False
            return False

    def _request_pairing(self) -> None:
        with self._thread_lock:
            self._close_pairing_session()
            sock = self._open_socket(None)
            sock.settimeout(READ_TIMEOUT)
            self._send_hex(sock, PAIRING_REQUEST)
            self._read_some(sock, READ_TIMEOUT)
            self._pairing_session = _PairingSession(sock)

    def _submit_pin(self, pin: str) -> PairingResult:
        with self._thread_lock:
            session = self._pairing_session
            if session is None:
                sock = self._open_socket(None)
                sock.settimeout(READ_TIMEOUT)
                self._send_hex(sock, PAIRING_REQUEST)
                self._read_some(sock, READ_TIMEOUT)
                session = _PairingSession(sock)
                self._pairing_session = session

            try:
                self._send_hex(session.socket, _build_pin_payload(pin))
                payload = self._read_until(
                    session.socket,
                    lambda data: PAIRING_CERT_PREFIX in data and SUCCESS_TEXT in data,
                    PAIRING_READ_TIMEOUT,
                )
                credentials = _extract_pairing_credentials(payload)
                self.credentials = credentials
                return PairingResult(credentials=credentials)
            finally:
                self._close_pairing_session()

    def _send_key(self, key: str) -> None:
        self._send_keys([key])

    def _send_keys(self, keys: list[str]) -> None:
        if self.credentials is None:
            raise ShieldNotPairedError("Shield has not been paired")

        payloads: list[str] = []
        for key in keys:
            normalized = key.upper().replace("-", "_").replace(" ", "_")
            if normalized not in KEY_PAYLOADS:
                raise ShieldProtocolError(f"Unsupported Shield key: {key}")
            payloads.extend(KEY_PAYLOADS[normalized])

        sock = self._open_socket(self.credentials)
        try:
            sock.settimeout(READ_TIMEOUT)
            self._send_hex(sock, LOGIN_REQUEST)
            self._read_until(
                sock,
                lambda data: LOGIN_SUCCESS_PREFIX in data,
                READ_TIMEOUT,
            )
            for payload in payloads:
                self._send_hex(sock, payload)
                self._read_some(sock, 0.2)
            self.last_available = True
        finally:
            sock.close()

    def _open_socket(self, credentials: ShieldCredentials | None) -> ssl.SSLSocket:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        cert_pem: str
        key_pem: str
        if credentials is None:
            cert_pem, key_pem = _generate_bootstrap_certificate()
        else:
            cert_pem = credentials.cert_pem
            key_pem = credentials.key_pem

        try:
            with tempfile.TemporaryDirectory(prefix="ha-shield-") as tmpdir:
                cert_path = os.path.join(tmpdir, "client.crt")
                key_path = os.path.join(tmpdir, "client.key")
                _write_private_file(cert_path, cert_pem)
                _write_private_file(key_path, key_pem)
                context.load_cert_chain(cert_path, key_path)

            raw_sock = socket.create_connection(
                (self.host, self.port), timeout=CONNECT_TIMEOUT
            )
            try:
                return context.wrap_socket(raw_sock, server_hostname=self.host)
            except Exception:
                raw_sock.close()
                raise
        except ssl.SSLError as err:
            self.last_available = False
            raise ShieldConnectionError("Unable to establish Shield TLS session") from err
        except OSError as err:
            self.last_available = False
            raise ShieldConnectionError(
                f"Unable to connect to Shield at {self.host}:{self.port}"
            ) from err

    def _close_pairing_session(self) -> None:
        session = self._pairing_session
        if session is not None:
            session.close()
            self._pairing_session = None

    @staticmethod
    def _send_hex(sock: ssl.SSLSocket, payload: str) -> None:
        sock.sendall(bytes.fromhex(payload))

    @staticmethod
    def _read_some(sock: ssl.SSLSocket, timeout: float) -> bytes:
        old_timeout = sock.gettimeout()
        sock.settimeout(timeout)
        try:
            try:
                return sock.recv(8192)
            except socket.timeout:
                return b""
        finally:
            sock.settimeout(old_timeout)

    @staticmethod
    def _read_until(
        sock: ssl.SSLSocket,
        predicate,
        timeout: float,
    ) -> bytes:
        old_timeout = sock.gettimeout()
        sock.settimeout(0.5)
        end = datetime.now(timezone.utc) + timedelta(seconds=timeout)
        payload = bytearray()
        try:
            while datetime.now(timezone.utc) < end:
                try:
                    chunk = sock.recv(8192)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                payload.extend(chunk)
                if predicate(bytes(payload)):
                    return bytes(payload)
        finally:
            sock.settimeout(old_timeout)

        raise ShieldProtocolError("Timed out waiting for Shield response")


def _build_pin_payload(pin: str) -> str:
    clean_pin = pin.strip()
    if not clean_pin:
        raise ShieldProtocolError("PIN cannot be empty")
    encoded_pin = clean_pin.encode("ascii").hex()
    return (
        "080a121f08d108121a0a06"
        + encoded_pin
        + "121036646564646461326639366635646261"
    )


def _extract_pairing_credentials(payload: bytes) -> ShieldCredentials:
    success_at = payload.find(SUCCESS_TEXT)
    if success_at < 0:
        raise ShieldProtocolError("Pairing response did not contain success marker")

    field_at = payload.find(b"\x1a", success_at + len(SUCCESS_TEXT))
    if field_at < 0:
        raise ShieldProtocolError("Pairing response did not contain key field")

    key_len, key_start = _read_varint(payload, field_at + 1)
    key_end = key_start + key_len
    key_der = payload[key_start:key_end]
    if len(key_der) != key_len:
        raise ShieldProtocolError("Pairing response key field was truncated")

    cert_start = key_end
    while cert_start < len(payload) and payload[cert_start] != 0x30:
        cert_start += 1
    if cert_start >= len(payload):
        raise ShieldProtocolError("Pairing response did not contain certificate")

    cert_len = _der_total_length(payload, cert_start)
    cert_der = payload[cert_start : cert_start + cert_len]

    try:
        private_key = serialization.load_der_private_key(key_der, None)
        cert = x509.load_der_x509_certificate(cert_der)
    except ValueError as err:
        raise ShieldProtocolError("Pairing response contained invalid credentials") from err

    key_pem = private_key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    ).decode("ascii")
    cert_pem = cert.public_bytes(Encoding.PEM).decode("ascii")
    return ShieldCredentials(cert_pem=cert_pem, key_pem=key_pem)


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    shift = 0
    value = 0
    index = offset
    while index < len(data):
        byte = data[index]
        value |= (byte & 0x7F) << shift
        index += 1
        if not byte & 0x80:
            return value, index
        shift += 7
        if shift > 63:
            break
    raise ShieldProtocolError("Invalid protobuf varint")


def _der_total_length(data: bytes, offset: int) -> int:
    if offset >= len(data) or data[offset] != 0x30:
        raise ShieldProtocolError("DER object does not start with a sequence")
    length_byte = data[offset + 1]
    if length_byte < 0x80:
        return 2 + length_byte
    octets = length_byte & 0x7F
    if octets == 0 or octets > 4:
        raise ShieldProtocolError("Unsupported DER length")
    start = offset + 2
    end = start + octets
    length = int.from_bytes(data[start:end], "big")
    return 2 + octets + length


def _generate_bootstrap_certificate() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "Home Assistant Shield Remote"),
        ]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(Encoding.PEM).decode("ascii")
    key_pem = key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    ).decode("ascii")
    return cert_pem, key_pem


def _write_private_file(path: str, data: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(data)
