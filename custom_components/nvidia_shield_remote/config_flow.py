"""Config flow for NVIDIA Shield Remote."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.zeroconf import ZeroconfServiceInfo
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT

from .const import (
    ATTR_PIN,
    CONF_CLIENT_CERT,
    CONF_CLIENT_KEY,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DOMAIN,
)
from .protocol import (
    PairingResult,
    ShieldConnectionError,
    ShieldProtocolClient,
    ShieldProtocolError,
)


class NvidiaShieldRemoteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an NVIDIA Shield Remote config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovered: dict[str, Any] | None = None
        self._pairing_data: dict[str, Any] | None = None
        self._pairing_client: ShieldProtocolClient | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle manual setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            port = int(user_input[CONF_PORT])
            if port < 1 or port > 65535:
                errors["base"] = "invalid_port"
            else:
                host = user_input[CONF_HOST].strip()
                await self.async_set_unique_id(host.lower())
                self._abort_if_unique_id_configured()
                data = {
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_HOST: host,
                    CONF_PORT: port,
                }
                return await self._async_start_pairing(data, "user")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
                }
            ),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> config_entries.ConfigFlowResult:
        """Handle mDNS discovery."""
        host = discovery_info.host
        port = discovery_info.port or DEFAULT_PORT
        name = DEFAULT_NAME
        properties = discovery_info.properties or {}
        server = properties.get("SERVER") or properties.get(b"SERVER")
        unique_id = _decode_property(server) or host.lower()

        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})

        self._discovered = {
            CONF_NAME: name,
            CONF_HOST: host,
            CONF_PORT: port,
        }
        self.context["title_placeholders"] = {
            "name": name,
            "host": host,
            "port": str(port),
        }
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm discovered setup."""
        if self._discovered is None:
            return await self.async_step_user(user_input)

        if user_input is not None:
            return await self._async_start_pairing(
                self._discovered,
                "zeroconf_confirm",
            )

        return self.async_show_form(step_id="zeroconf_confirm")

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle pairing again when an existing entry has no credentials."""
        entry = self._get_reauth_entry()
        data = {
            CONF_NAME: entry.data.get(CONF_NAME, entry.title),
            CONF_HOST: entry.data[CONF_HOST],
            CONF_PORT: entry.data.get(CONF_PORT, DEFAULT_PORT),
        }
        return await self._async_start_pairing(data, "reauth")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle reconfiguring and re-pairing an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            port = int(user_input[CONF_PORT])
            if port < 1 or port > 65535:
                errors["base"] = "invalid_port"
            else:
                data = {
                    CONF_NAME: user_input[CONF_NAME],
                    CONF_HOST: user_input[CONF_HOST].strip(),
                    CONF_PORT: port,
                }
                return await self._async_start_pairing(data, "reconfigure")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME,
                        default=entry.data.get(CONF_NAME, entry.title),
                    ): str,
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                    vol.Required(
                        CONF_PORT,
                        default=entry.data.get(CONF_PORT, DEFAULT_PORT),
                    ): vol.Coerce(int),
                }
            ),
            errors=errors,
        )

    async def async_step_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the Shield PIN shown on screen."""
        if self._pairing_data is None or self._pairing_client is None:
            return await self.async_step_user()

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                result = await self._pairing_client.async_submit_pin(
                    user_input[ATTR_PIN]
                )
            except ShieldConnectionError:
                errors["base"] = "cannot_connect"
            except ShieldProtocolError:
                errors["base"] = "invalid_pin"
            else:
                return self._async_finish_pairing(result)

        return self.async_show_form(
            step_id="pin",
            data_schema=vol.Schema({vol.Required(ATTR_PIN): str}),
            errors=errors,
            description_placeholders={
                "name": self._pairing_data[CONF_NAME],
                "host": self._pairing_data[CONF_HOST],
            },
            last_step=True,
        )

    async def _async_start_pairing(
        self,
        data: dict[str, Any],
        error_step_id: str,
    ) -> config_entries.ConfigFlowResult:
        """Request a Shield pairing PIN and show the PIN entry form."""
        self._pairing_data = data
        self._pairing_client = ShieldProtocolClient(
            data[CONF_HOST],
            data.get(CONF_PORT, DEFAULT_PORT),
        )
        try:
            await self._pairing_client.async_request_pairing()
        except ShieldConnectionError:
            return self._show_start_error(error_step_id, "cannot_connect")
        except ShieldProtocolError:
            return self._show_start_error(error_step_id, "unknown")

        return await self.async_step_pin()

    def _async_finish_pairing(
        self,
        result: PairingResult,
    ) -> config_entries.ConfigFlowResult:
        """Create or update a config entry after successful pairing."""
        if self._pairing_data is None:
            return self.async_abort(reason="unknown")

        data = {
            **self._pairing_data,
            CONF_CLIENT_CERT: result.credentials.cert_pem,
            CONF_CLIENT_KEY: result.credentials.key_pem,
        }

        if self.source == config_entries.SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(),
                data=data,
            )

        if self.source == config_entries.SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                title=data[CONF_NAME],
                data=data,
            )

        return self.async_create_entry(title=data[CONF_NAME], data=data)

    def _show_start_error(
        self,
        step_id: str,
        error: str,
    ) -> config_entries.ConfigFlowResult:
        """Show an error on the setup step that requested pairing."""
        if step_id == "zeroconf_confirm":
            return self.async_show_form(
                step_id="zeroconf_confirm",
                errors={"base": error},
            )
        if step_id == "reconfigure":
            return self.async_show_form(
                step_id="reconfigure",
                errors={"base": error},
                data_schema=_reconfigure_schema(self._pairing_data),
            )
        if step_id == "reauth":
            return self.async_show_form(
                step_id="reauth",
                errors={"base": error},
            )
        return self.async_show_form(
            step_id="user",
            errors={"base": error},
            data_schema=_user_schema(self._pairing_data),
        )


def _decode_property(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")
    return str(value)


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the manual setup schema."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME,
                default=defaults.get(CONF_NAME, DEFAULT_NAME),
            ): str,
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(
                CONF_PORT,
                default=defaults.get(CONF_PORT, DEFAULT_PORT),
            ): vol.Coerce(int),
        }
    )


def _reconfigure_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the reconfigure schema."""
    return _user_schema(defaults)
