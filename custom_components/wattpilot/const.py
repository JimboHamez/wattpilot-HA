"""Constants for Fronius Wattpilot."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "wattpilot"
FUNC_CONNECTION_MONITOR: Final = "connection_monitor"
FUNC_OPTION_UPDATES: Final = "options_update_listener"
FUNC_PROPERTY_UPDATES_CALLBACK: Final = "property_updates_callback"
SUPPORTED_PLATFORMS: Final = ["button", "number", "select", "sensor", "switch", "update"]

DEFAULT_NAME: Final = "Wattpilot"
CONF_DBG_PROPS: Final = "debug_properties"
CONF_CHARGERS: Final = "chargers"
CONF_CHARGER: Final = "charger"
CONF_CLOUD_API: Final = "cloud_api"
CONF_CLOUD: Final = "cloud"
CONF_CONNECTION: Final = "connection"
CONF_LOCAL: Final = "local"
CONF_SERIAL: Final = "serial"

DEFAULT_TIMEOUT: Final = 15

# How often the charger connection state is sampled to log availability changes.
# Matches Home Assistant's default entity scan interval, so an entity going
# unavailable and the log entry explaining why land at roughly the same time.
AVAILABILITY_SCAN_INTERVAL: Final = timedelta(seconds=30)

EVENT_PROPS_ID: Final = DOMAIN + "_property_message"
EVENT_PROPS: Final = ["ftt", "cak"]

CLOUD_API_URL_PREFIX: Final = "https://"
CLOUD_API_URL_POSTFIX: Final = ".api.v3.go-e.io/api/"
