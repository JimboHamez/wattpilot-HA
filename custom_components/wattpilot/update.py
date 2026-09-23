"""Update entities for the Fronius Wattpilot integration."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any, Final

from packaging.version import Version

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.const import CONF_TIMEOUT
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .catalog import async_setup_catalog_entities
from .const import DEFAULT_TIMEOUT, DOMAIN
from .entities import ChargerPlatformEntity
from .utils import GetChargerProp

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .models import WattpilotConfigEntry

_LOGGER: Final = logging.getLogger(__name__)
platform = "update"
PARALLEL_UPDATES = 0  # local push over a single WebSocket; no rate limit needed


async def async_setup_entry(
    hass: HomeAssistant, entry: WattpilotConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the update platform."""
    await async_setup_catalog_entities(
        hass,
        entry,
        async_add_entities,
        platform,
        ChargerUpdate,
        source="property",
        required=("id", "id_installed", "id_trigger", "source"),
    )


class ChargerUpdate(ChargerPlatformEntity, UpdateEntity):
    """Update class for Fronius Wattpilot integration."""

    _state_attr = "_attr_latest_version"
    _dummy_version = "0.0.1"

    def _init_platform_specific(self) -> None:
        """Platform specific init actions."""
        _LOGGER.debug("%s - %s: _init_platform_specific", self._charger_id, self._identifier)
        self._available_versions: dict[str, str] = {}
        self._identifier_installed = self._entity_cfg.get("id_installed")
        self._identifier_trigger = self._entity_cfg.get("id_trigger", None)
        self._identifier_status = self._entity_cfg.get("id_status", None)

        self._attr_installed_version = GetChargerProp(self._charger, self._identifier_installed, None)
        self._attr_latest_version = self._update_available_versions(None, True)

        if self._identifier_trigger is not None:
            self._attr_supported_features |= UpdateEntityFeature.INSTALL
            self._attr_supported_features |= UpdateEntityFeature.SPECIFIC_VERSION
        # if not self._identifier_status is None: #wattpilot disconnects during update
        #    self._attr_supported_features |= UpdateEntityFeature.PROGRESS
        _LOGGER.debug("%s - %s: _init_platform_specific complete", self._charger_id, self._identifier)

    def _update_available_versions(self, v_list: Any = None, return_latest: bool = False) -> Any:
        """Get the latest update version of available versions."""
        _LOGGER.debug("%s - %s: _update_available_versions", self._charger_id, self._identifier)
        try:
            if v_list is None:
                v_list = GetChargerProp(self._charger, self._identifier, None)
            if v_list is None and hasattr(self, "_attr_installed_version") and self._attr_installed_version is not None:
                v_list = [self._attr_installed_version]
            elif v_list is None:
                v_list = [self._dummy_version]
            elif not isinstance(v_list, list):
                v_list = [v_list]
            self._available_versions = self._get_versions_dict(v_list) or {}
            latest = list(self._available_versions.keys())
            latest.sort(key=Version)
            return latest[-1]
        except Exception as e:
            _LOGGER.exception(
                "%s - %s: _get_versions_dict failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            if return_latest:
                return self._dummy_version
            return None

    def _get_versions_dict(self, v_list: list[str]) -> dict[str, str] | None:
        """Create a dict with clean and named versions."""
        _LOGGER.debug("%s - %s: _get_versions_dict", self._charger_id, self._identifier)
        try:
            # Map a cleaned, PEP 440-parseable version onto the raw charger
            # version string. The charger reports free-form names (e.g.
            # "V1.2.3-beta4"); strip the "v"/"version" prefix and any trailing
            # junk down to "1.2.3beta4" so packaging.version.Version can sort
            # them, while keeping the original name to send back on install.
            versions = {}
            for v in v_list:
                c = (v.lower()).replace("x", "0")
                c = re.sub(
                    r"^(v|ver|vers|version)*\s*\.*\s*([0-9.x]*)\s*-?\s*((alpha|beta|dev|rc|post|a|b|release)+[0-9]*)?\s*.*$",
                    r"\2\3",
                    c,
                )
                versions[c] = v
            return versions
        except Exception as e:
            _LOGGER.exception(
                "%s - %s: _get_versions_dict failed: %s (%s.%s)",
                self._charger_id,
                self._identifier,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Trigger update install."""
        try:
            _LOGGER.debug("%s - %s: async_install: update charger to: %s", self._charger_id, self._identifier, version)
            if version is None:
                version = self._attr_latest_version
            if version is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="update_no_version",
                    translation_placeholders={"entity": str(self.entity_id)},
                )
            v_name = self._available_versions.get(version, None)
            if v_name is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="update_unknown_version",
                    translation_placeholders={
                        "entity": str(self.entity_id),
                        "version": str(version),
                        "versions": ", ".join(str(v) for v in self._available_versions),
                    },
                )
            _LOGGER.debug(
                "%s - %s: async_install: trigger charger update via: %s -> %s",
                self._charger_id,
                self._identifier,
                self._identifier_trigger,
                v_name,
            )
            # INSTALL is only advertised when id_trigger is set, so it is never None here.
            await self._async_write_property(
                str(self._identifier_trigger), v_name, force=True, force_type=self._set_type
            )
            # Resolve the configured connection timeout (falling back to the
            # default). A firmware flash plus reboot takes far longer than a
            # normal connect, so allow up to 4x that budget below.
            timeout = self._entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT) * 4
            # The charger drops its WebSocket while flashing: first wait for it
            # to disconnect (update started), then wait for it to reconnect.
            timer = 0
            while timeout > timer and self._charger.connected:
                await asyncio.sleep(1)
                timer += 1
            if self._charger.connected:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="update_install_timeout",
                    translation_placeholders={"entity": str(self.entity_id), "timeout": str(timeout)},
                )
            _LOGGER.debug(
                "%s - %s: async_install: charger disconnected - waiting for reconnect",
                self._charger_id,
                self._identifier,
            )
            timer = 0
            while timeout > timer and not self._charger.connected:
                await asyncio.sleep(1)
                timer += 1
            if not self._charger.connected:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="update_restart_timeout",
                    translation_placeholders={"entity": str(self.entity_id), "timeout": str(timeout)},
                )
        except HomeAssistantError:
            raise
        except Exception as e:
            raise self._action_failure("async_install", e) from e

    async def _async_update_validate_platform_state(self, state: Any = None) -> Any:
        """Async: Validate the given state for sensor specific requirements."""
        _LOGGER.debug("%s - %s: _async_update_validate_platform_state", self._charger_id, self._identifier)
        self._attr_installed_version = GetChargerProp(self._charger, self._identifier_installed, None)
        state = await self.hass.async_add_executor_job(self._update_available_versions, state, True)
        _LOGGER.debug(
            "%s - %s: _async_update_validate_platform_state: state: %s", self._charger_id, self._identifier, state
        )
        return state
