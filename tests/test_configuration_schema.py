"""Tests for the config/options flow schemas."""

from __future__ import annotations

import logging

import pytest
import voluptuous as vol

pytest.importorskip("homeassistant")
from homeassistant.const import CONF_FRIENDLY_NAME, CONF_IP_ADDRESS, CONF_PASSWORD, CONF_TIMEOUT

from custom_components.wattpilot.configuration_schema import (
    CLOUD_SCHEMA,
    LOCAL_SCHEMA,
    async_get_OPTIONS_CLOUD_SCHEMA,
    async_get_OPTIONS_LOCAL_SCHEMA,
)
from custom_components.wattpilot.const import CONF_SERIAL, DEFAULT_TIMEOUT


def _defaults(schema: vol.Schema) -> dict:
    """Return the default value of every key in a schema."""
    return {str(key): key.default() for key in schema.schema if key.default is not vol.UNDEFINED}


async def test_local_options_schema_prefills_the_current_settings():
    """The local options form opens on the values already configured."""
    schema = await async_get_OPTIONS_LOCAL_SCHEMA(
        {CONF_FRIENDLY_NAME: "WB", CONF_IP_ADDRESS: "1.2.3.4", CONF_PASSWORD: "p", CONF_TIMEOUT: 30}
    )

    defaults = _defaults(schema)
    assert defaults[CONF_IP_ADDRESS] == "1.2.3.4"
    assert defaults[CONF_FRIENDLY_NAME] == "WB"
    assert defaults[CONF_TIMEOUT] == 30


async def test_cloud_options_schema_prefills_the_current_settings():
    """The cloud options form opens on the values already configured."""
    schema = await async_get_OPTIONS_CLOUD_SCHEMA({CONF_FRIENDLY_NAME: "WB", CONF_SERIAL: "SN", CONF_PASSWORD: "p"})

    defaults = _defaults(schema)
    assert defaults[CONF_SERIAL] == "SN"
    assert defaults[CONF_TIMEOUT] == DEFAULT_TIMEOUT


@pytest.mark.parametrize(
    ("builder", "fallback"),
    [(async_get_OPTIONS_LOCAL_SCHEMA, LOCAL_SCHEMA), (async_get_OPTIONS_CLOUD_SCHEMA, CLOUD_SCHEMA)],
)
async def test_options_schema_falls_back_to_the_blank_form(caplog, builder, fallback):
    """Unreadable current settings still produce a usable (blank) form."""
    with caplog.at_level(logging.ERROR, logger="custom_components.wattpilot.configuration_schema"):
        schema = await builder(None)

    assert schema is fallback
    assert any("failed" in r.getMessage() for r in caplog.records)
