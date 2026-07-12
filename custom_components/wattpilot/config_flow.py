"""Config flow for Fronius Wattpilot."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar, Final

import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant import config_entries
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_IP_ADDRESS, CONF_PASSWORD, CONF_TIMEOUT
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow

from . import options_update_listener
from .configuration_schema import (
    CLOUD_SCHEMA,
    CONNECTION_SCHEMA,
    LOCAL_SCHEMA,
    async_get_OPTIONS_CLOUD_SCHEMA,
    async_get_OPTIONS_LOCAL_SCHEMA,
)
from .const import CONF_CLOUD, CONF_CONNECTION, CONF_LOCAL, CONF_SERIAL, DEFAULT_NAME, DEFAULT_TIMEOUT, DOMAIN

if TYPE_CHECKING:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

REDACT_CONFIG = {CONF_PASSWORD}

_LOGGER: Final = logging.getLogger(__name__)


class ConfigFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Custom config flow."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH
    data: dict[str, Any] | None
    loaded_platforms: ClassVar[list] = []

    def __init__(self):
        """Initialize."""
        _LOGGER.debug("%s - ConfigFlowHandler: __init__", DOMAIN)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Invoked when a user initiates a flow via the user interface."""
        _LOGGER.debug(
            "%s - ConfigFlowHandler: async_step_user: %s", DOMAIN, async_redact_data(user_input, REDACT_CONFIG)
        )
        try:
            if not hasattr(self, "data"):
                self.data = {}
            return await self.async_step_connection()
        except Exception as e:
            _LOGGER.error(
                "%s - ConfigFlowHandler: async_step_user failed: %s (%s.%s)",
                DOMAIN,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return self.async_abort(reason="exception")

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo):
        """Handle a charger discovered on the network via mDNS/zeroconf."""
        _LOGGER.debug("%s - ConfigFlowHandler: async_step_zeroconf: %s", DOMAIN, discovery_info.host)
        try:
            props = discovery_info.properties
            serial = props.get("serial")
            if not serial:
                return self.async_abort(reason="no_serial")
            # The serial uniquely identifies the charger; abort (and refresh the
            # stored IP) if it is already configured.
            await self.async_set_unique_id(str(serial))
            self._abort_if_unique_id_configured(updates={CONF_IP_ADDRESS: discovery_info.host})
            name = props.get("friendly_name") or discovery_info.hostname.removesuffix(".local.")
            self.data = {
                CONF_CONNECTION: CONF_LOCAL,
                CONF_IP_ADDRESS: discovery_info.host,
                CONF_SERIAL: str(serial),
                CONF_FRIENDLY_NAME: name,
            }
            # Show the friendly name on the discovery card and confirm dialog.
            self.context["title_placeholders"] = {"name": name}
            return await self.async_step_zeroconf_confirm()
        except AbortFlow:
            raise
        except Exception as e:
            _LOGGER.error(
                "%s - ConfigFlowHandler: async_step_zeroconf failed: %s (%s.%s)",
                DOMAIN,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return self.async_abort(reason="exception")

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None):
        """Ask the user for the password of a discovered charger."""
        _LOGGER.debug("%s - ConfigFlowHandler: async_step_zeroconf_confirm", DOMAIN)
        try:
            name = self.data.get(CONF_FRIENDLY_NAME, DEFAULT_NAME)
            if user_input is not None:
                self.data[CONF_PASSWORD] = user_input[CONF_PASSWORD]
                self.data[CONF_TIMEOUT] = user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
                return self.async_create_entry(title=name, data=self.data)
            schema = vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): cv.string,
                    vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
                }
            )
            return self.async_show_form(
                step_id="zeroconf_confirm", data_schema=schema, description_placeholders={"name": name}
            )
        except Exception as e:
            _LOGGER.error(
                "%s - ConfigFlowHandler: async_step_zeroconf_confirm failed: %s (%s.%s)",
                DOMAIN,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return self.async_abort(reason="exception")

    async def async_step_connection(self, user_input: dict[str, Any] | None = None):
        """Config flow to define a charger connection via user interface."""
        _LOGGER.debug(
            "%s - ConfigFlowHandler: async_step_connection: %s", DOMAIN, async_redact_data(user_input, REDACT_CONFIG)
        )
        try:
            errors: dict[str, str] = {}
            if user_input is not None:
                _LOGGER.debug(
                    "%s - ConfigFlowHandler: async_step_connection add user_input to data: %s",
                    DOMAIN,
                    async_redact_data(user_input, REDACT_CONFIG),
                )
                if user_input[CONF_CONNECTION] == CONF_LOCAL:
                    return await self.async_step_local()
                elif user_input[CONF_CONNECTION] == CONF_CLOUD:
                    return await self.async_step_cloud()
            return self.async_show_form(
                step_id=CONF_CONNECTION, data_schema=CONNECTION_SCHEMA, errors=errors
            )  # via the "step_id" the function calls itself after GUI completion
        except Exception as e:
            _LOGGER.error(
                "%s - ConfigFlowHandler: async_step_connection failed: %s (%s.%s)",
                DOMAIN,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return self.async_abort(reason="exception")

    async def async_step_local(self, user_input: dict[str, Any] | None = None):
        """Config flow to define a local charger connection via user interface."""
        _LOGGER.debug(
            "%s - ConfigFlowHandler: async_step_local: %s", DOMAIN, async_redact_data(user_input, REDACT_CONFIG)
        )
        try:
            errors: dict[str, str] = {}
            if user_input is not None:
                _LOGGER.debug(
                    "%s - ConfigFlowHandler: async_step_local add user_input to data: %s",
                    DOMAIN,
                    async_redact_data(user_input, REDACT_CONFIG),
                )
                user_input[CONF_CONNECTION] = CONF_LOCAL
                # Prevent the same charger (identified by its local IP) from
                # being configured twice.
                await self.async_set_unique_id(str(user_input[CONF_IP_ADDRESS]))
                self._abort_if_unique_id_configured()
                self.data = user_input
                return await self.async_step_final()
            return self.async_show_form(
                step_id=CONF_LOCAL, data_schema=LOCAL_SCHEMA, errors=errors
            )  # via the "step_id" the function calls itself after GUI completion
        except AbortFlow:
            # Control-flow signal from _abort_if_unique_id_configured(); must
            # propagate so the flow aborts with its real reason, not "exception".
            raise
        except Exception as e:
            _LOGGER.error(
                "%s - ConfigFlowHandler: async_step_local failed: %s (%s.%s)",
                DOMAIN,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return self.async_abort(reason="exception")

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None):
        """Config flow to define a cloud charger connection via user interface."""
        _LOGGER.debug(
            "%s - ConfigFlowHandler: async_step_cloud: %s", DOMAIN, async_redact_data(user_input, REDACT_CONFIG)
        )
        try:
            errors: dict[str, str] = {}
            if user_input is not None:
                _LOGGER.debug(
                    "%s - ConfigFlowHandler: async_step_cloud add user_input to data: %s",
                    DOMAIN,
                    async_redact_data(user_input, REDACT_CONFIG),
                )
                user_input[CONF_CONNECTION] = CONF_CLOUD
                # Prevent the same charger (identified by its serial) from
                # being configured twice.
                await self.async_set_unique_id(str(user_input[CONF_SERIAL]))
                self._abort_if_unique_id_configured()
                self.data = user_input
                return await self.async_step_final()
            return self.async_show_form(
                step_id=CONF_CLOUD, data_schema=CLOUD_SCHEMA, errors=errors
            )  # via the "step_id" the function calls itself after GUI completion
        except AbortFlow:
            # Control-flow signal from _abort_if_unique_id_configured(); must
            # propagate so the flow aborts with its real reason, not "exception".
            raise
        except Exception as e:
            _LOGGER.error(
                "%s - ConfigFlowHandler: async_step_cloud failed: %s (%s.%s)",
                DOMAIN,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return self.async_abort(reason="exception")

    async def async_step_final(self, user_input: dict[str, Any] | None = None):
        """Create the config entry from the collected connection details."""
        _LOGGER.debug(
            "%s - ConfigFlowHandler: async_step_final: %s", DOMAIN, async_redact_data(user_input, REDACT_CONFIG)
        )
        data = self.data or {}
        title = data.get(CONF_FRIENDLY_NAME, data.get(CONF_IP_ADDRESS, DEFAULT_NAME))
        return self.async_create_entry(title=title, data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow handler for this integration."""
        _LOGGER.debug("%s: ConfigFlowHandler - async_get_options_flow", DOMAIN)
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handles options flow for the component."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        _LOGGER.debug("%s - OptionsFlowHandler: __init__: %s", DOMAIN, config_entry)
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Manage the options for the custom component."""
        _LOGGER.debug("%s - OptionsFlowHandler: async_step_init: %s", DOMAIN, user_input)
        try:
            if not hasattr(self, "data"):
                self.data = {}
            if self._config_entry.source == config_entries.SOURCE_USER:
                return await self.async_step_config_connection()
            else:
                _LOGGER.warning(
                    "%s - OptionsFlowHandler: async_step_init: source not supported: %s",
                    DOMAIN,
                    self._config_entry.source,
                )
                return self.async_abort(reason="not_supported")
        except Exception as e:
            _LOGGER.error(
                "%s - OptionsFlowHandler: async_step_init failed: %s (%s.%s)",
                DOMAIN,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return self.async_abort(reason="exception")

    async def async_step_config_connection(self, user_input: dict[str, Any] | None = None):
        """Options flow: choose the connection type to reconfigure."""
        _LOGGER.debug(
            "%s - OptionsFlowHandler: async_step_config_connection: %s",
            DOMAIN,
            async_redact_data(user_input, REDACT_CONFIG),
        )
        try:
            if not user_input:
                return self.async_show_form(step_id="config_connection", data_schema=CONNECTION_SCHEMA)
            _LOGGER.debug(
                "%s - OptionsFlowHandler: async_step_config_connection - user_input: %s",
                DOMAIN,
                async_redact_data(user_input, REDACT_CONFIG),
            )
            if user_input[CONF_CONNECTION] == CONF_LOCAL:
                return await self.async_step_config_local()
            elif user_input[CONF_CONNECTION] == CONF_CLOUD:
                return await self.async_step_config_cloud()
        except Exception as e:
            _LOGGER.error(
                "%s - OptionsFlowHandler: async_step_config_connection failed: %s (%s.%s)",
                DOMAIN,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return self.async_abort(reason="exception")

    async def async_step_config_local(self, user_input=None):
        """Options flow: update the local connection details."""
        _LOGGER.debug(
            "%s - OptionsFlowHandler: async_step_config_local: %s", DOMAIN, async_redact_data(user_input, REDACT_CONFIG)
        )
        try:
            OPTIONS_LOCAL_SCHEMA = await async_get_OPTIONS_LOCAL_SCHEMA(self._config_entry.data)
            if not user_input:
                return self.async_show_form(step_id="config_local", data_schema=OPTIONS_LOCAL_SCHEMA)
            _LOGGER.debug(
                "%s - OptionsFlowHandler: async_step_config_local - user_input: %s",
                DOMAIN,
                async_redact_data(user_input, REDACT_CONFIG),
            )
            user_input[CONF_CONNECTION] = CONF_LOCAL
            self.data.update(user_input)
            _LOGGER.debug(
                "%s - OptionsFlowHandler: async_step_config_local complete: %s",
                DOMAIN,
                async_redact_data(user_input, REDACT_CONFIG),
            )
            return await self.async_step_final()
        except Exception as e:
            _LOGGER.error(
                "%s - OptionsFlowHandler: async_step_config_local failed: %s (%s.%s)",
                DOMAIN,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return self.async_abort(reason="exception")

    async def async_step_config_cloud(self, user_input=None):
        """Options flow: update the cloud connection details."""
        _LOGGER.debug(
            "%s - OptionsFlowHandler: async_step_config_cloud: %s", DOMAIN, async_redact_data(user_input, REDACT_CONFIG)
        )
        try:
            OPTIONS_CLOUD_SCHEMA = await async_get_OPTIONS_CLOUD_SCHEMA(self._config_entry.data)
            if not user_input:
                return self.async_show_form(step_id="config_cloud", data_schema=OPTIONS_CLOUD_SCHEMA)
            _LOGGER.debug(
                "%s - OptionsFlowHandler: async_step_config_cloud - user_input: %s",
                DOMAIN,
                async_redact_data(user_input, REDACT_CONFIG),
            )
            user_input[CONF_CONNECTION] = CONF_CLOUD
            self.data.update(user_input)
            _LOGGER.debug(
                "%s - OptionsFlowHandler: async_step_config_cloud complete: %s",
                DOMAIN,
                async_redact_data(user_input, REDACT_CONFIG),
            )
            return await self.async_step_final()
        except Exception as e:
            _LOGGER.error(
                "%s - OptionsFlowHandler: async_step_config_cloud failed: %s (%s.%s)",
                DOMAIN,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return self.async_abort(reason="exception")

    async def async_step_final(self):
        """Persist the updated options and reload the entry if needed."""
        try:
            _LOGGER.debug("%s - OptionsFlowHandler: async_step_final", DOMAIN)
            title = self.data.get(CONF_FRIENDLY_NAME, self.data.get(CONF_IP_ADDRESS, DEFAULT_NAME))
            if self._config_entry.state is config_entries.ConfigEntryState.SETUP_ERROR:
                _LOGGER.debug(
                    "%s - OptionsFlowHandler: in errorstate - trigger execution of options_update_listener", DOMAIN
                )
                await options_update_listener(self.hass, self._config_entry)
            return self.async_create_entry(title=title, data=self.data)
        except Exception as e:
            _LOGGER.error(
                "%s - OptionsFlowHandler: async_step_final failed: %s (%s.%s)",
                DOMAIN,
                str(e),
                e.__class__.__module__,
                type(e).__name__,
            )
            return self.async_abort(reason="exception")
