"""Tests for the service actions.

Covers the quality-scale ``action-exceptions`` rule: a bad call raises
``ServiceValidationError`` and a charger-side failure raises
``HomeAssistantError``, instead of the log-and-degrade behaviour used elsewhere
in the integration. Services are driven through ``hass.services.async_call`` so
registration and device-id resolution are exercised too.

Errors are asserted by ``translation_key`` rather than by message text
(quality-scale rule ``exception-translations``); that the keys resolve to real
messages is checked here too, and their catalog coverage in
``test_exception_translations.py``.
"""

from __future__ import annotations

import datetime
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

pytest.importorskip("pytest_homeassistant_custom_component")
from homeassistant.const import CONF_IP_ADDRESS, CONF_PASSWORD
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattpilot.const import CONF_CONNECTION, CONF_DBG_PROPS, CONF_LOCAL, DOMAIN

PROPS = {
    "tma": 5.0,
    "car": 1,
    "amp": 6,
    "nrg": [0] * 16,
    "frc": 0,
    "typ": "model",
    "var": 11,
    "sse": "SN",
    "ftt": 0,
    "tds": 0,
    "cae": False,
    "sch_week": SimpleNamespace(
        control=3,
        ranges=[
            SimpleNamespace(
                begin=SimpleNamespace(hour=0, minute=0, second=0), end=SimpleNamespace(hour=6, minute=0, second=0)
            )
        ],
    ),
}


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Allow the custom component under custom_components/ to be loaded."""
    yield


@pytest.fixture
async def charger_device(hass, make_charger):
    """Set up an entry with a mock charger; yield the charger and its device id."""
    charger = make_charger(props=dict(PROPS), serial="SN", name="WB", cak="cloud-key")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CONNECTION: CONF_LOCAL, CONF_IP_ADDRESS: "1.2.3.4", "friendly_name": "WB", CONF_PASSWORD: "p"},
    )
    entry.add_to_hass(hass)

    with patch("custom_components.wattpilot.async_ConnectCharger", new=AsyncMock(return_value=charger)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "SN")})
    assert device is not None
    yield charger, device.id, entry

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_set_next_trip_writes_the_timestamp(hass, charger_device):
    """A valid call writes the computed next-trip timestamp to the charger."""
    charger, device_id, _entry = charger_device

    await hass.services.async_call(
        DOMAIN, "set_next_trip", {"device_id": device_id, "trigger_time": "07:30:00"}, blocking=True
    )

    expected = int(time.mktime(datetime.datetime.strptime("1970-01-01 07:30:00", "%Y-%m-%d %H:%M:%S").timetuple()))
    assert ("ftt", expected) in charger.sent


async def test_set_next_trip_applies_daylight_saving(hass, charger_device):
    """With tds == 1 the charger's daylight-saving offset is added."""
    charger, device_id, _entry = charger_device
    charger.all_properties["tds"] = 1

    await hass.services.async_call(
        DOMAIN, "set_next_trip", {"device_id": device_id, "trigger_time": "07:30:00"}, blocking=True
    )

    expected = (
        int(time.mktime(datetime.datetime.strptime("1970-01-01 07:30:00", "%Y-%m-%d %H:%M:%S").timetuple())) + 3600
    )
    assert ("ftt", expected) in charger.sent


async def test_set_next_trip_missing_parameter_raises(hass, charger_device):
    """A missing required parameter is reported as a validation error."""
    _charger, device_id, _entry = charger_device

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "set_next_trip", {"device_id": device_id}, blocking=True)
    assert err.value.translation_key == "missing_parameter"


@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("set_next_trip", {"trigger_time": "07:30:00"}),
        ("set_goe_cloud", {"cloud_api": True}),
        ("set_debug_properties", {"debug_properties": True}),
        ("disconnect_charger", {}),
    ],
)
async def test_services_refuse_a_charger_whose_entry_is_not_loaded(hass, charger_device, service, data):
    """An unloaded entry keeps its old runtime data; a call must not reach that stale charger (action-setup)."""
    charger, device_id, entry = charger_device
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    sent_before = list(charger.sent)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, service, {"device_id": device_id, **data}, blocking=True)
    assert err.value.translation_key == "entry_not_loaded"
    assert charger.sent == sent_before


async def test_services_reject_an_unknown_field(hass, charger_device):
    """The service schema rejects a field the action does not have, before the handler runs."""
    charger, device_id, _entry = charger_device

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "set_next_trip", {"device_id": device_id, "trigger_tme": "07:30:00"}, blocking=True
        )
    assert not any(key == "ftt" for key, _value in charger.sent)


async def test_set_next_trip_unknown_device_raises(hass, charger_device):
    """An unresolvable device id is reported as a validation error."""
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "set_next_trip", {"device_id": "does-not-exist", "trigger_time": "07:30:00"}, blocking=True
        )
    assert err.value.translation_key == "charger_not_found"


async def test_set_next_trip_invalid_time_raises(hass, charger_device):
    """An unparseable trigger time is reported as a validation error."""
    _charger, device_id, _entry = charger_device

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "set_next_trip", {"device_id": device_id, "trigger_time": "half past seven"}, blocking=True
        )
    assert err.value.translation_key == "invalid_trigger_time"


async def test_set_next_trip_write_failure_raises(hass, charger_device):
    """A rejected property write surfaces as a Home Assistant error."""
    _charger, device_id, _entry = charger_device

    with (
        patch("custom_components.wattpilot.services.async_SetChargerProp", new=AsyncMock(return_value=False)),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(
            DOMAIN, "set_next_trip", {"device_id": device_id, "trigger_time": "07:30:00"}, blocking=True
        )
    assert err.value.translation_key == "set_next_trip_failed"


async def test_set_debug_properties_updates_the_data_store(hass, charger_device):
    """Valid debug states are stored on the config entry runtime data."""
    _charger, device_id, entry = charger_device

    await hass.services.async_call(
        DOMAIN, "set_debug_properties", {"device_id": device_id, CONF_DBG_PROPS: True}, blocking=True
    )
    assert entry.runtime_data.debug_properties is True

    await hass.services.async_call(
        DOMAIN, "set_debug_properties", {"device_id": device_id, CONF_DBG_PROPS: ["amp", "frc"]}, blocking=True
    )
    assert entry.runtime_data.debug_properties == ["amp", "frc"]


async def test_set_debug_properties_invalid_state_raises(hass, charger_device):
    """A debug state that is neither bool, bool-like string nor list is rejected."""
    _charger, device_id, _entry = charger_device

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "set_debug_properties", {"device_id": device_id, CONF_DBG_PROPS: 42}, blocking=True
        )
    assert err.value.translation_key == "invalid_debug_properties"


async def test_raised_keys_resolve_to_translated_messages(hass, charger_device):
    """Home Assistant serves the exception catalog, so a raise carries a real message."""
    from homeassistant.helpers.translation import async_get_translations

    _charger, device_id, _entry = charger_device

    served = await async_get_translations(hass, "en", "exceptions", [DOMAIN])
    assert served[f"component.{DOMAIN}.exceptions.missing_parameter.message"] == "{parameter} is a required parameter."

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "set_next_trip", {"device_id": device_id}, blocking=True)

    # The placeholder is filled in and the bare key never reaches the user.
    assert str(err.value) == "trigger_time is a required parameter"


async def test_disconnect_charger_closes_the_session(hass, charger_device):
    """Disconnecting closes the charger session."""
    charger, device_id, _entry = charger_device

    await hass.services.async_call(DOMAIN, "disconnect_charger", {"device_id": device_id}, blocking=True)
    assert charger.connected is False


async def test_disconnect_charger_failure_raises(hass, charger_device):
    """A client-side disconnect failure surfaces as a Home Assistant error."""
    charger, device_id, _entry = charger_device
    charger.disconnect = AsyncMock(side_effect=OSError("boom"))

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, "disconnect_charger", {"device_id": device_id}, blocking=True)
    assert err.value.translation_key == "service_failed"


async def test_reconnect_charger_reuses_the_charger_object(hass, charger_device):
    """Reconnecting disconnects first and reuses the existing charger object."""
    charger, device_id, _entry = charger_device

    with patch(
        "custom_components.wattpilot.services.async_ConnectCharger", new=AsyncMock(return_value=charger)
    ) as connect:
        await hass.services.async_call(DOMAIN, "reconnect_charger", {"device_id": device_id}, blocking=True)

    assert charger.connected is False, "the session should have been closed before reconnecting"
    assert connect.call_args.args[2] is charger


async def test_reconnect_charger_failure_raises(hass, charger_device):
    """A failed reconnect surfaces as a Home Assistant error."""
    _charger, device_id, _entry = charger_device

    with (
        patch("custom_components.wattpilot.services.async_ConnectCharger", new=AsyncMock(return_value=False)),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(DOMAIN, "reconnect_charger", {"device_id": device_id}, blocking=True)
    assert err.value.translation_key == "reconnect_failed"


async def test_set_goe_cloud_enable_stores_key_and_url(hass, charger_device):
    """Enabling the cloud API caches the returned key and the API URL."""
    _charger, device_id, entry = charger_device

    await hass.services.async_call(DOMAIN, "set_goe_cloud", {"device_id": device_id, "cloud_api": True}, blocking=True)

    assert entry.runtime_data.cloud_api_key == "cloud-key"
    assert entry.runtime_data.cloud_api_url.endswith(".api.v3.go-e.io/api/")


async def test_set_goe_cloud_key_timeout_raises(hass, charger_device):
    """No API key within the timeout surfaces as a Home Assistant error."""
    charger, device_id, entry = charger_device
    charger.cak = ""

    # The handler polls with one-second sleeps; skip the real waiting.
    with (
        patch("custom_components.wattpilot.services.asyncio.sleep", new=AsyncMock()),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(
            DOMAIN, "set_goe_cloud", {"device_id": device_id, "cloud_api": True}, blocking=True
        )
    assert err.value.translation_key == "cloud_api_key_timeout"

    assert entry.runtime_data.cloud_api_key is False


async def test_set_goe_cloud_write_failure_raises(hass, charger_device):
    """A rejected cloud-API write surfaces as a Home Assistant error."""
    _charger, device_id, _entry = charger_device

    with (
        patch("custom_components.wattpilot.services.async_SetChargerProp", new=AsyncMock(return_value=False)),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(
            DOMAIN, "set_goe_cloud", {"device_id": device_id, "cloud_api": False}, blocking=True
        )
    assert err.value.translation_key == "cloud_api_disable_failed"


async def test_service_registration_failure_is_logged(caplog):
    """A failure while registering a service is logged, not raised."""
    from custom_components.wattpilot.services import async_registerService

    hass = MagicMock()
    hass.services.has_service.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.services"):
        await async_registerService(hass, "set_next_trip", AsyncMock())

    assert any("async_registerService: failed" in r.getMessage() for r in caplog.records)


async def test_registering_an_existing_service_is_skipped():
    """Re-registering an existing service is a no-op."""
    from custom_components.wattpilot.services import async_registerService

    hass = MagicMock()
    hass.services.has_service.return_value = True

    await async_registerService(hass, "set_next_trip", AsyncMock())

    hass.services.async_register.assert_not_called()


@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("set_next_trip", {"trigger_time": "07:30:00"}),
        ("set_goe_cloud", {"cloud_api": True}),
        ("set_debug_properties", {CONF_DBG_PROPS: True}),
        ("set_charging_schedule", {"day_type": "weekdays", "limit_charging_times": True}),
        ("reconnect_charger", {}),
        ("disconnect_charger", {}),
    ],
)
async def test_every_service_requires_a_device(hass, charger_device, service, data):
    """Each service rejects a call that names no device."""
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, service, dict(data), blocking=True)
    assert err.value.translation_key == "missing_parameter"


@pytest.mark.parametrize(
    ("service", "data", "target"),
    [
        ("set_next_trip", {"trigger_time": "07:30:00"}, "async_GetChargerProp"),
        ("set_goe_cloud", {"cloud_api": True}, "async_SetChargerProp"),
        ("set_charging_schedule", {"day_type": "weekdays", "limit_charging_times": True}, "async_GetChargerProp"),
        ("reconnect_charger", {}, "async_ConnectCharger"),
    ],
)
async def test_unexpected_failures_become_home_assistant_errors(hass, charger_device, service, data, target):
    """An unexpected error inside a service is wrapped, logged and raised."""
    _charger, device_id, _entry = charger_device

    with (
        patch(f"custom_components.wattpilot.services.{target}", side_effect=RuntimeError("boom")),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(DOMAIN, service, {"device_id": device_id, **data}, blocking=True)
    assert err.value.translation_key == "service_failed"


async def test_set_debug_properties_accepts_bool_like_strings(hass, charger_device):
    """'true' and 'false' strings are accepted as debug states."""
    _charger, device_id, entry = charger_device

    await hass.services.async_call(
        DOMAIN, "set_debug_properties", {"device_id": device_id, CONF_DBG_PROPS: "true"}, blocking=True
    )
    assert entry.runtime_data.debug_properties is True

    await hass.services.async_call(
        DOMAIN, "set_debug_properties", {"device_id": device_id, CONF_DBG_PROPS: "false"}, blocking=True
    )
    assert entry.runtime_data.debug_properties is False


async def test_set_goe_cloud_enable_failure_raises(hass, charger_device):
    """A charger refusing to enable the cloud API surfaces as an error."""
    _charger, device_id, _entry = charger_device

    with (
        patch("custom_components.wattpilot.services.async_SetChargerProp", new=AsyncMock(return_value=False)),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(
            DOMAIN, "set_goe_cloud", {"device_id": device_id, "cloud_api": True}, blocking=True
        )
    assert err.value.translation_key == "cloud_api_enable_failed"


async def test_reconnect_charger_skips_disconnect_when_already_offline(hass, charger_device):
    """A charger that is already offline is reconnected without disconnecting."""
    charger, device_id, _entry = charger_device
    charger.connected = False
    charger.disconnect = AsyncMock()

    with patch("custom_components.wattpilot.services.async_ConnectCharger", new=AsyncMock(return_value=charger)):
        await hass.services.async_call(DOMAIN, "reconnect_charger", {"device_id": device_id}, blocking=True)

    charger.disconnect.assert_not_awaited()


# --- set_charging_schedule ------------------------------------------------------


async def test_set_charging_schedule_writes_the_whole_object(hass, charger_device):
    """A valid call rewrites the day type's schedule with the supplied field changed."""
    charger, device_id, _entry = charger_device

    await hass.services.async_call(
        DOMAIN,
        "set_charging_schedule",
        {"device_id": device_id, "day_type": "weekdays", "pv_surplus_outside_times": False},
        blocking=True,
    )

    assert charger.sent[-1] == (
        "sch_week",
        {
            "control": 1,
            "ranges": [
                {"begin": {"hour": 0, "minute": 0, "second": 0}, "end": {"hour": 6, "minute": 0, "second": 0}},
            ],
        },
    )


async def test_set_charging_schedule_requires_a_day_type(hass, charger_device):
    """Without a day type there is nothing to address."""
    _charger, device_id, _entry = charger_device

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "set_charging_schedule", {"device_id": device_id, "limit_charging_times": True}, blocking=True
        )
    assert err.value.translation_key == "missing_parameter"


async def test_set_charging_schedule_rejects_an_unknown_day_type(hass, charger_device):
    """A day type outside weekdays/saturday/sunday is a validation error."""
    _charger, device_id, _entry = charger_device

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_charging_schedule",
            {"device_id": device_id, "day_type": "monday", "limit_charging_times": True},
            blocking=True,
        )
    assert err.value.translation_key == "invalid_day_type"


async def test_set_charging_schedule_rejects_a_non_boolean_flag(hass, charger_device):
    """A flag that is neither a bool nor "true"/"false" is a validation error."""
    _charger, device_id, _entry = charger_device

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_charging_schedule",
            {"device_id": device_id, "day_type": "weekdays", "limit_charging_times": "maybe"},
            blocking=True,
        )
    assert err.value.translation_key == "invalid_boolean"


@pytest.mark.parametrize(
    "ranges",
    [
        "00:00-06:00",
        [{"begin": "00:00"}],
        ["00:00-06:00"],
        [{"begin": "00:00", "end": "six"}],
        [{"begin": 0, "end": "06:00"}],
    ],
)
async def test_set_charging_schedule_rejects_malformed_ranges(hass, charger_device, ranges):
    """Ranges must be a list of begin/end times of day."""
    _charger, device_id, _entry = charger_device

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_charging_schedule",
            {"device_id": device_id, "day_type": "weekdays", "ranges": ranges},
            blocking=True,
        )
    assert err.value.translation_key == "invalid_schedule_ranges"


@pytest.mark.parametrize(
    ("ranges", "key"),
    [
        ([{"begin": "22:00", "end": "06:00"}], "schedule_range_crosses_midnight"),
        ([{"begin": "08:00", "end": "08:00"}], "schedule_range_crosses_midnight"),
        ([{"begin": "00:00", "end": "06:00"}, {"begin": "05:30", "end": "08:00"}], "schedule_ranges_overlap"),
    ],
)
async def test_set_charging_schedule_rejects_windows_before_writing(hass, charger_device, ranges, key):
    """A window into the next day, or overlapping windows, is an error and nothing reaches the charger."""
    charger, device_id, _entry = charger_device

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_charging_schedule",
            {"device_id": device_id, "day_type": "weekdays", "ranges": ranges},
            blocking=True,
        )
    assert err.value.translation_key == key
    assert not [sent for sent in charger.sent if sent[0] == "sch_week"]


async def test_set_charging_schedule_needs_something_to_change(hass, charger_device):
    """A call that supplies none of the schedule fields is a validation error."""
    _charger, device_id, _entry = charger_device

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, "set_charging_schedule", {"device_id": device_id, "day_type": "weekdays"}, blocking=True
        )
    assert err.value.translation_key == "schedule_nothing_to_set"


async def test_set_charging_schedule_on_a_charger_without_one_raises(hass, charger_device):
    """A charger that does not report the schedule property cannot take a schedule."""
    _charger, device_id, _entry = charger_device

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            "set_charging_schedule",
            {"device_id": device_id, "day_type": "saturday", "limit_charging_times": True},
            blocking=True,
        )
    assert err.value.translation_key == "schedule_not_supported"


async def test_set_charging_schedule_write_failure_raises(hass, charger_device):
    """A rejected property write surfaces as a Home Assistant error."""
    _charger, device_id, _entry = charger_device

    with (
        patch("custom_components.wattpilot.services.async_SetChargerProp", new=AsyncMock(return_value=False)),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(
            DOMAIN,
            "set_charging_schedule",
            {"device_id": device_id, "day_type": "weekdays", "limit_charging_times": False},
            blocking=True,
        )
    assert err.value.translation_key == "set_schedule_failed"
