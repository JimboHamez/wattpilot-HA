"""Move a charger's config entry and entities onto serial-based unique ids.

Before 0.12.0 a manually added local entry was keyed by its IP address, and every
entity by the entry's friendly name (else its IP). Home Assistant rules out both as
unique-id sources: two chargers left at the default name collided, so the second
one's entities were dropped, and renaming a charger orphaned all of its entities.
Both are now keyed by the serial number the charger reports.

The serial is only known once the charger is connected, so this runs from
``async_setup_entry`` - after connecting, before the platforms add any entity -
rather than from ``async_migrate_entry``. It is idempotent: an entry and entities
already on the serial are left alone, so it costs one registry scan per setup.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

from .const import CONF_SERIAL, DOMAIN
from .utils import legacy_unique_prefix

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .models import WattpilotConfigEntry

_LOGGER: Final = logging.getLogger(__name__)


async def async_migrate_unique_ids(hass: HomeAssistant, entry: WattpilotConfigEntry, serial: str) -> None:
    """Move the entry's entities, then the entry itself, onto the charger's serial.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry being set up.
        serial: The serial number the connected charger reports.
    """
    # Entities first: their old prefix is read from the entry's data, which the
    # entry migration below extends.
    await _async_migrate_entity_unique_ids(hass, entry, serial)
    _async_migrate_entry_unique_id(hass, entry, serial)


async def _async_migrate_entity_unique_ids(hass: HomeAssistant, entry: WattpilotConfigEntry, serial: str) -> None:
    """Rename ``<name or IP>-<uid>`` entity unique ids to ``<serial>-<uid>``.

    Only entities under the entry's current prefix are moved. An entity orphaned by
    an earlier rename carries an older prefix; it has no live counterpart to keep
    continuity with, so it is left for the user to remove. An id that is already
    taken is skipped with a warning rather than failing the whole setup.
    """
    old_prefix = f"{legacy_unique_prefix(entry.data)}-"
    new_prefix = f"{serial}-"
    if old_prefix == new_prefix:
        return
    ent_reg = er.async_get(hass)

    @callback
    def _migrate(reg_entry: er.RegistryEntry) -> dict[str, Any] | None:
        if not reg_entry.unique_id.startswith(old_prefix):
            return None
        new_unique_id = new_prefix + reg_entry.unique_id[len(old_prefix) :]
        if ent_reg.async_get_entity_id(reg_entry.domain, reg_entry.platform, new_unique_id):
            _LOGGER.warning(
                "%s - _async_migrate_entity_unique_ids: not moving %s, its new unique id is taken: %s",
                entry.entry_id,
                reg_entry.entity_id,
                new_unique_id,
            )
            return None
        _LOGGER.debug(
            "%s - _async_migrate_entity_unique_ids: %s: %s -> %s",
            entry.entry_id,
            reg_entry.entity_id,
            reg_entry.unique_id,
            new_unique_id,
        )
        return {"new_unique_id": new_unique_id}

    await er.async_migrate_entries(hass, entry.entry_id, _migrate)


@callback
def _async_migrate_entry_unique_id(hass: HomeAssistant, entry: WattpilotConfigEntry, serial: str) -> None:
    """Key an IP-keyed entry by the charger's serial, and store the serial.

    Only an entry keyed by its IP address (a manually added local charger) is
    moved. Cloud and discovered entries were created with the serial already. The
    stored serial also lets reconfiguration check that it reaches the same charger.
    If another entry already holds this serial, the same charger is configured
    twice; the entry is left as it is and the duplicate is reported.
    """
    if entry.unique_id == serial or entry.unique_id != entry.data.get(CONF_IP_ADDRESS):
        return
    duplicate = next(
        (
            other
            for other in hass.config_entries.async_entries(DOMAIN)
            if other.entry_id != entry.entry_id and other.unique_id == serial
        ),
        None,
    )
    if duplicate is not None:
        _LOGGER.warning(
            "%s - _async_migrate_entry_unique_id: charger %s is also configured as %s; remove one of the two entries",
            entry.entry_id,
            serial,
            duplicate.title,
        )
        return
    _LOGGER.info("%s - _async_migrate_entry_unique_id: keying the entry by serial %s", entry.entry_id, serial)
    hass.config_entries.async_update_entry(entry, unique_id=serial, data={**entry.data, CONF_SERIAL: serial})
