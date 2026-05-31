"""Config flow for NVIDIA Shield Remote."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.zeroconf import ZeroconfServiceInfo
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT

from .const import DEFAULT_NAME, DEFAULT_PORT, DOMAIN


class NvidiaShieldRemoteConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle an NVIDIA Shield Remote config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovered: dict[str, Any] | None = None

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
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_HOST: host,
                        CONF_PORT: port,
                    },
                )

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
            return self.async_create_entry(
                title=self._discovered[CONF_NAME],
                data=self._discovered,
            )

        return self.async_show_form(step_id="zeroconf_confirm")


def _decode_property(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", "ignore")
    return str(value)
