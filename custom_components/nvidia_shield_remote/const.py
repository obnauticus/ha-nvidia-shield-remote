"""Constants for the NVIDIA Shield Remote integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "nvidia_shield_remote"

DEFAULT_NAME = "NVIDIA Shield"
DEFAULT_PORT = 8987

CONF_CLIENT_CERT = "client_cert"
CONF_CLIENT_KEY = "client_key"

DATA_RUNTIMES = "runtimes"
DATA_ENTITY_MAP = "entity_map"

PLATFORMS = [Platform.REMOTE]

SERVICE_REQUEST_PAIRING = "request_pairing"
SERVICE_SUBMIT_PIN = "submit_pin"
SERVICE_SEND_KEY = "send_key"
SERVICE_WAKE = "wake"
SERVICE_SLEEP = "sleep"

ATTR_KEY = "key"
ATTR_PIN = "pin"

ZEROCONF_TYPE = "_nv_shield_remote._tcp.local."
