"""Button entities for the Fronius Wattpilot integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from homeassistant.components.button import ButtonEntity

from .catalog import async_setup_catalog_entities
from .entities import ChargerPlatformEntity
from .utils import async_SetChargerProp

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .models import WattpilotConfigEntry

_LOGGER: Final = logging.getLogger(__name__)
platform = "button"
PARALLEL_UPDATES = 0  # local push over a single WebSocket; no rate limit needed


async def async_setup_entry(
    hass: HomeAssistant, entry: WattpilotConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the button platform."""
    await async_setup_catalog_entities(hass, entry, async_add_entities, platform, ChargerButton, source="none")


class ChargerButton(ChargerPlatformEntity, ButtonEntity):
    """Button class for Fronius Wattpilot integration."""

    def _init_platform_specific(self) -> None:
        """Platform specific init actions."""
        self._set_value = self._entity_cfg.get("set_value", None)
        if self._set_value is None:
            _LOGGER.error(
                "%s - %s: __init__: Required configuration option 'set_value' missing - please specify: %s",
                self._charger_id,
                self._identifier,
                self._set_value,
            )
            return None

    async def async_local_poll(self) -> None:
        """Async: Poll the latest data and states from the entity."""
        # no state required for ButtonEntity
        pass

    async def async_press(self) -> None:
        """Async: Handle button press."""
        try:
            await async_SetChargerProp(
                self._charger, self._identifier, self._set_value, force=True, force_type=self._set_type
            )
        except Exception as e:
            _LOGGER.exception(
                "%s - %s: update failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
