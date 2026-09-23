"""Typed per-entry runtime data for the Fronius Wattpilot integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from wattpilot_api import Wattpilot


@dataclass
class WattpilotRuntimeData:
    """What a loaded config entry keeps on ``entry.runtime_data``.

    Attributes:
        charger: The connected ``Wattpilot`` client.
        params: The entry's connection details, as they were when it was set up.
        debug_properties: Property-change debug logging: ``True`` for every
            property, a list for only those, ``False`` for none. Set by the
            ``set_debug_properties`` action.
        property_updates_unsub: Unsubscribes the property-change callback.
        connection_monitor_cancel: Stops the connection monitor's timer.
        cloud_api_key: The go-e cloud API key the charger returned, ``False``
            once the cloud API was disabled or no key arrived. Set by the
            ``set_goe_cloud`` action.
        cloud_api_url: The go-e cloud API URL for this charger, set alongside
            ``cloud_api_key``.
    """

    charger: Wattpilot
    params: Mapping[str, Any]
    debug_properties: bool | list[str] = False
    property_updates_unsub: Callable[[], None] | None = None
    connection_monitor_cancel: Callable[[], None] | None = None
    cloud_api_key: str | Literal[False] | None = None
    cloud_api_url: str | None = None


type WattpilotConfigEntry = ConfigEntry[WattpilotRuntimeData]
