"""Remote platform for NVIDIA Shield Remote."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import RemoteEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ShieldRuntime
from .const import DATA_ENTITY_MAP, DATA_RUNTIMES, DOMAIN
from .protocol import ShieldConnectionError, ShieldNotPairedError, ShieldProtocolError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Shield remote entity."""
    runtime: ShieldRuntime = hass.data[DOMAIN][DATA_RUNTIMES][entry.entry_id]
    async_add_entities([NvidiaShieldRemoteEntity(entry, runtime)])


class NvidiaShieldRemoteEntity(RemoteEntity):
    """Remote entity backed by NVIDIA's Shield app protocol."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, runtime: ShieldRuntime) -> None:
        """Initialize the remote entity."""
        self._entry = entry
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_remote"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="NVIDIA",
            name=entry.data.get(CONF_NAME, "NVIDIA Shield"),
        )

    @property
    def available(self) -> bool:
        """Return whether the last protocol operation could reach the Shield."""
        return self._runtime.client.last_available

    @property
    def is_on(self) -> bool | None:
        """Return the assumed Shield power state."""
        return self._runtime.client.assumed_on

    async def async_added_to_hass(self) -> None:
        """Register entity to config entry mapping for helper services."""
        if self.entity_id is not None:
            self.hass.data[DOMAIN][DATA_ENTITY_MAP][self.entity_id] = (
                self._entry.entry_id
            )

    async def async_will_remove_from_hass(self) -> None:
        """Remove entity mapping."""
        if self.entity_id is not None:
            self.hass.data[DOMAIN][DATA_ENTITY_MAP].pop(self.entity_id, None)

    async def async_turn_on(
        self, activity: str | None = None, **kwargs: Any
    ) -> None:
        """Send Shield wake command."""
        await self._call(self._runtime.client.async_wake)

    async def async_turn_off(
        self, activity: str | None = None, **kwargs: Any
    ) -> None:
        """Send Shield standby command."""
        await self._call(self._runtime.client.async_sleep)

    async def async_toggle(
        self, activity: str | None = None, **kwargs: Any
    ) -> None:
        """Toggle Shield power."""
        await self._call(self._runtime.client.async_power_toggle)

    async def async_send_command(
        self, command: Iterable[str], **kwargs: Any
    ) -> None:
        """Send one or more Shield remote commands."""
        if isinstance(command, str):
            commands = [command]
        else:
            commands = list(command)
        repeats = int(kwargs.get("num_repeats", 1) or 1)
        delay = float(kwargs.get("delay_secs", 0) or 0)
        for repeat in range(repeats):
            await self._call(self._runtime.client.async_send_keys, commands)
            if delay > 0 and repeat < repeats - 1:
                await asyncio.sleep(delay)

    async def _call(self, func, *args: Any) -> None:
        try:
            await func(*args)
        except ShieldNotPairedError as err:
            raise HomeAssistantError(
                "NVIDIA Shield is not paired. Call nvidia_shield_remote.request_pairing "
                "and nvidia_shield_remote.submit_pin first."
            ) from err
        except (ShieldConnectionError, ShieldProtocolError) as err:
            raise HomeAssistantError(str(err)) from err
        finally:
            self.async_write_ha_state()
