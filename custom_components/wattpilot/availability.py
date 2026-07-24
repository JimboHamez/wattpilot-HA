"""Connection availability logging for the Fronius Wattpilot integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

from .const import AVAILABILITY_SCAN_INTERVAL, DEFAULT_NAME

if TYPE_CHECKING:
    from datetime import datetime

    from wattpilot_api import Wattpilot

    from homeassistant.core import CALLBACK_TYPE, HomeAssistant

_LOGGER: Final = logging.getLogger(__name__)


def charger_available(charger: Wattpilot) -> bool:
    """Return whether the charger connection can currently serve values.

    Args:
        charger: The connected ``Wattpilot`` client.

    Returns:
        True while the WebSocket is up and the property set is initialized.
    """
    # Both flags default to True when absent, matching the same two checks in
    # ChargerPlatformEntity.available: a client that does not expose them must
    # not be reported as offline.
    return bool(getattr(charger, "connected", True) and getattr(charger, "properties_initialized", True))


class ChargerConnectionMonitor:
    """Log once when a charger becomes unavailable and once when it returns.

    The ``wattpilot_api`` client reconnects its WebSocket on its own and offers
    no connection-state callback, so the state is sampled on a timer. Only
    transitions are logged: a charger that stays offline for hours produces one
    warning, not one per sample.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str, charger: Wattpilot) -> None:
        """Initialize the monitor for an already connected charger.

        Args:
            hass: The Home Assistant instance.
            entry_id: The config entry id, used as the log prefix.
            charger: The connected ``Wattpilot`` client to watch.
        """
        self._hass = hass
        self._entry_id = entry_id
        self._charger = charger
        # Setup only reaches this point once the charger is connected, so the
        # first lost connection is a transition and gets logged.
        self._available = True

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Start sampling the connection state.

        Returns:
            The callable that stops the sampling timer again.
        """
        _LOGGER.debug("%s - async_start: monitoring charger connection state", self._entry_id)
        return async_track_time_interval(self._hass, self._async_check, AVAILABILITY_SCAN_INTERVAL)

    async def _async_check(self, now: datetime | None = None) -> None:
        """Sample the connection state and log a change since the last sample."""
        try:
            available = charger_available(self._charger)
            if available is self._available:
                return None
            self._available = available
            name = getattr(self._charger, "name", DEFAULT_NAME)
            if available:
                _LOGGER.info("%s - _async_check: connection to charger re-established: %s", self._entry_id, name)
            else:
                _LOGGER.warning(
                    "%s - _async_check: connection to charger lost, entities stay unavailable until it reconnects: %s",
                    self._entry_id,
                    name,
                )
            return None
        except Exception as e:
            _LOGGER.error(
                "%s - _async_check: checking charger connection state failed: %s (%s.%s)",
                self._entry_id,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return None
