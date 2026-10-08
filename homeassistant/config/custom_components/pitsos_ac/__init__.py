"""A climate entity per Pitsos air conditioner on top of Home Connect.

Home Connect has its own climate platform, but it skips these units: their
programs are select-only, so it sees nothing to start. This wraps the power
switch and the selected program select that Home Connect does give.

The developer API client HA logs in with may not write the setpoint
(SDK.Error.UnsupportedOption), so options go to the API with the official
app's OAuth client instead: `pitsos_ac: token:` in configuration.yaml is the
blob hc_app_auth.py prints. It lands in the config entry, which also keeps
the refreshed tokens.
"""
import base64
import json

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.helpers import config_validation as cv

DOMAIN = "pitsos_ac"
CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({vol.Required("token"): cv.string})}, extra=vol.ALLOW_EXTRA)


def decode(blob):
    return {"blob": blob, **json.loads(base64.b64decode(blob))}


async def async_setup(hass, config):
    if DOMAIN not in config:
        return True
    blob = config[DOMAIN]["token"]
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        hass.async_create_task(hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=decode(blob)))
    elif entries[0].data.get("blob") != blob:  # a new login replaces the tokens
        hass.config_entries.async_update_entry(entries[0], data=decode(blob))
    return True


async def async_setup_entry(hass, entry):
    await hass.config_entries.async_forward_entry_setups(entry, ["climate"])
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, ["climate"])
