"""The NVIDIA Shield Remote integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_KEY,
    ATTR_PIN,
    CONF_CLIENT_CERT,
    CONF_CLIENT_KEY,
    DATA_ENTITY_MAP,
    DATA_RUNTIMES,
    DEFAULT_PORT,
    DOMAIN,
    PLATFORMS,
    SERVICE_REQUEST_PAIRING,
    SERVICE_SEND_KEY,
    SERVICE_SLEEP,
    SERVICE_SUBMIT_PIN,
    SERVICE_WAKE,
)
from .protocol import (
    ShieldConnectionError,
    ShieldCredentials,
    ShieldNotPairedError,
    ShieldProtocolClient,
    ShieldProtocolError,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ShieldRuntime:
    """Runtime data for a Shield config entry."""

    entry: ConfigEntry
    client: ShieldProtocolClient


SERVICE_ENTITY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_ids,
    }
)

SERVICE_PIN_SCHEMA = SERVICE_ENTITY_SCHEMA.extend(
    {
        vol.Required(ATTR_PIN): cv.string,
    }
)

SERVICE_KEY_SCHEMA = SERVICE_ENTITY_SCHEMA.extend(
    {
        vol.Required(ATTR_KEY): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration services."""
    _ensure_data(hass)
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up NVIDIA Shield Remote from a config entry."""
    _ensure_data(hass)
    _register_services(hass)

    credentials = None
    if entry.data.get(CONF_CLIENT_CERT) and entry.data.get(CONF_CLIENT_KEY):
        credentials = ShieldCredentials(
            cert_pem=entry.data[CONF_CLIENT_CERT],
            key_pem=entry.data[CONF_CLIENT_KEY],
        )
    else:
        raise ConfigEntryAuthFailed("NVIDIA Shield is not paired")

    client = ShieldProtocolClient(
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        credentials=credentials,
    )
    hass.data[DOMAIN][DATA_RUNTIMES][entry.entry_id] = ShieldRuntime(entry, client)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Shield config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime = hass.data[DOMAIN][DATA_RUNTIMES].pop(entry.entry_id, None)
        if runtime is not None:
            runtime.client.close()
        entity_map = hass.data[DOMAIN][DATA_ENTITY_MAP]
        for entity_id, entry_id in list(entity_map.items()):
            if entry_id == entry.entry_id:
                entity_map.pop(entity_id, None)
    return unload_ok


def _register_services(hass: HomeAssistant) -> None:
    """Register integration-level helper services once."""
    if hass.services.has_service(DOMAIN, SERVICE_WAKE):
        return

    async def handle_request_pairing(call: ServiceCall) -> None:
        for runtime in _runtimes_from_call(hass, call):
            await _call_client(runtime.client.async_request_pairing)

    async def handle_submit_pin(call: ServiceCall) -> None:
        pin = call.data[ATTR_PIN]
        for runtime in _runtimes_from_call(hass, call):
            result = await _call_client(runtime.client.async_submit_pin, pin)
            data = {
                **runtime.entry.data,
                CONF_CLIENT_CERT: result.credentials.cert_pem,
                CONF_CLIENT_KEY: result.credentials.key_pem,
            }
            hass.config_entries.async_update_entry(runtime.entry, data=data)

    async def handle_send_key(call: ServiceCall) -> None:
        key = call.data[ATTR_KEY]
        for runtime in _runtimes_from_call(hass, call):
            await _call_client(runtime.client.async_send_key, key)

    async def handle_wake(call: ServiceCall) -> None:
        for runtime in _runtimes_from_call(hass, call):
            await _call_client(runtime.client.async_wake)

    async def handle_sleep(call: ServiceCall) -> None:
        for runtime in _runtimes_from_call(hass, call):
            await _call_client(runtime.client.async_sleep)

    hass.services.async_register(
        DOMAIN,
        SERVICE_REQUEST_PAIRING,
        handle_request_pairing,
        schema=SERVICE_ENTITY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SUBMIT_PIN,
        handle_submit_pin,
        schema=SERVICE_PIN_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_KEY,
        handle_send_key,
        schema=SERVICE_KEY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_WAKE,
        handle_wake,
        schema=SERVICE_ENTITY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SLEEP,
        handle_sleep,
        schema=SERVICE_ENTITY_SCHEMA,
    )


def _runtimes_from_call(hass: HomeAssistant, call: ServiceCall) -> list[ShieldRuntime]:
    entity_ids = call.data.get(ATTR_ENTITY_ID)
    runtimes: dict[str, ShieldRuntime] = hass.data[DOMAIN][DATA_RUNTIMES]
    entity_map: dict[str, str] = hass.data[DOMAIN][DATA_ENTITY_MAP]

    if entity_ids:
        selected: list[ShieldRuntime] = []
        for entity_id in entity_ids:
            entry_id = entity_map.get(entity_id)
            if entry_id is None:
                raise HomeAssistantError(
                    f"{entity_id} is not an NVIDIA Shield Remote entity"
                )
            selected.append(runtimes[entry_id])
        return selected

    if len(runtimes) == 1:
        return list(runtimes.values())

    raise HomeAssistantError("entity_id is required when multiple Shields are configured")


async def _call_client(func, *args: Any) -> Any:
    try:
        return await func(*args)
    except ShieldNotPairedError as err:
        raise HomeAssistantError(
            "NVIDIA Shield is not paired. Call request_pairing and submit_pin first."
        ) from err
    except ShieldConnectionError as err:
        raise HomeAssistantError(str(err)) from err
    except ShieldProtocolError as err:
        raise HomeAssistantError(str(err)) from err
    except Exception as err:
        _LOGGER.exception("Unexpected NVIDIA Shield Remote error")
        raise HomeAssistantError("Unexpected NVIDIA Shield Remote error") from err


def _ensure_data(hass: HomeAssistant) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    data.setdefault(DATA_RUNTIMES, {})
    data.setdefault(DATA_ENTITY_MAP, {})
