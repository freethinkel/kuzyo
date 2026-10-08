import json

from homeassistant.components.climate import (
    ATTR_TEMPERATURE, FAN_AUTO, FAN_HIGH, FAN_LOW, FAN_MEDIUM, ClimateEntity, ClimateEntityFeature, HVACMode)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event

API = "https://api.home-connect.com"
HEADERS = {"Accept": "application/vnd.bsh.sdk.v1+json", "Content-Type": "application/vnd.bsh.sdk.v1+json"}
PROGRAM = "heating_ventilation_air_conditioning_air_conditioner_program_"
SETPOINT = "HeatingVentilationAirConditioning.AirConditioner.Option.SetpointTemperature"
FAN = "HeatingVentilationAirConditioning.AirConditioner.Option.FanSpeed"
LEVEL = "HeatingVentilationAirConditioning.AirConditioner.EnumType.FanSpeedLevel."
FANS = {"Level1": FAN_LOW, "Level2": FAN_MEDIUM, "Level3": FAN_HIGH, "Auto": FAN_AUTO}
LEVELS = {fan: level for level, fan in FANS.items()}
MODES = {"cool": HVACMode.COOL, "heat": HVACMode.HEAT, "auto": HVACMode.AUTO,
         "dry": HVACMode.DRY, "fan": HVACMode.FAN_ONLY}
PROGRAMS = {mode: program for program, mode in MODES.items()}


async def async_setup_entry(hass, entry, async_add_entities):
    ents, devs = er.async_get(hass), dr.async_get(hass)
    coordinators = hass.config_entries.async_entries("home_connect")[0].runtime_data.appliance_coordinators
    by_unique_id = {e.unique_id: e for e in ents.entities.values() if e.platform == "home_connect"}
    acs = []
    for unique_id, program in by_unique_id.items():
        ha_id, _, key = unique_id.partition("-")
        power = by_unique_id.get(f"{ha_id}-BSH.Common.Setting.PowerState")
        options = (program.capabilities or {}).get("options", [])
        if key == "BSH.Common.Root.SelectedProgram" and power and any(o.startswith(PROGRAM) for o in options):
            acs.append(AirConditioner(entry, ha_id, devs.async_get(program.device_id), power.entity_id,
                                      program.entity_id, coordinators[ha_id]))
    async_add_entities(acs)


class AirConditioner(ClimateEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_hvac_modes = [HVACMode.OFF, *MODES.values()]
    _attr_supported_features = (ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE
                                | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF)
    _attr_fan_modes = list(FANS.values())
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp, _attr_max_temp, _attr_target_temperature_step = 16, 30, 1

    def __init__(self, entry, ha_id, device, power, program, coordinator):
        self._attr_unique_id = f"{ha_id}-climate"
        self.device_entry = device
        self._entry, self._ha_id, self._power, self._program = entry, ha_id, power, program
        self._coordinator = coordinator
        self._options, self._setpoint_event = {}, None

    async def async_added_to_hass(self):
        self.async_on_remove(async_track_state_change_event(
            self.hass, [self._power, self._program], self._changed))
        self.async_on_remove(self._coordinator.async_add_listener(self._changed, SETPOINT))
        self.async_on_remove(self._coordinator.async_add_listener(
            lambda: self.hass.async_create_task(self._read()), "unknown"))
        await self._read()

    async def _read(self):
        # Fan speed has no aiohomeconnect key: HA files its events as an "unknown"
        # setting without the value's key, so any such event means read the options.
        try:
            response = await self._request("GET", "programs/selected")
            self._options = {o["key"]: o["value"] for o in response["data"]["options"]}
        except Exception:  # noqa: BLE001 - an offline unit is fine, the next event retries
            pass
        self._changed()

    async def _request(self, method, path, data=None):
        session = async_get_clientsession(self.hass)
        for retry in (False, True):
            response = await session.request(
                method, f"{API}/api/homeappliances/{self._ha_id}/{path}",
                data=json.dumps({"data": data}) if data else None,
                headers={**HEADERS, "Authorization": f"Bearer {self._entry.data['access_token']}"})
            if response.status != 401 or retry:
                break
            await self._refresh(session)
        if response.status >= 400:
            raise HomeAssistantError(f"Home Connect: {await response.text()}")
        return await response.json() if response.status != 204 else None

    async def _refresh(self, session):
        # the app's client is public (PKCE): no secret, and the refresh token stays
        response = await session.post(f"{API}/security/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": self._entry.data["refresh_token"],
            "client_id": self._entry.data["client_id"]})
        if response.status >= 400:
            raise HomeAssistantError(f"Home Connect login expired, run hc_app_auth.py: {await response.text()}")
        self.hass.config_entries.async_update_entry(
            self._entry, data={**self._entry.data, **await response.json()})

    @callback
    def _changed(self, _event=None):
        power, program = self.hass.states.get(self._power), self.hass.states.get(self._program)
        self._attr_available = power is not None and power.state in ("on", "off")
        on = self._attr_available and power.state == "on"
        self._attr_hvac_mode = MODES.get(program.state.removeprefix(PROGRAM)) if on and program else HVACMode.OFF
        # the coordinator keeps the last setpoint event; only a new one beats what we wrote
        if (setpoint := self._coordinator.data.events.get(SETPOINT)) is not self._setpoint_event:
            self._setpoint_event = setpoint
            self._options[SETPOINT] = setpoint.value
        self._attr_target_temperature = self._options.get(SETPOINT)
        self._attr_fan_mode = FANS.get(str(self._options.get(FAN)).removeprefix(LEVEL))
        self.async_write_ha_state()

    async def _call(self, domain, service, **data):
        await self.hass.services.async_call(domain, service, data, blocking=True)

    async def _set_option(self, key, value, **extra):
        # a unit in standby does not answer option writes, so wake it first
        if self.hvac_mode == HVACMode.OFF:
            await self.async_turn_on()
        await self._request("PUT", f"programs/selected/options/{key}", {"key": key, "value": value, **extra})
        self._options[key] = value
        self._changed()

    async def async_turn_on(self):
        await self._call("switch", "turn_on", entity_id=self._power)

    async def async_turn_off(self):
        await self._call("switch", "turn_off", entity_id=self._power)

    async def async_set_hvac_mode(self, hvac_mode):
        if hvac_mode == HVACMode.OFF:
            return await self.async_turn_off()
        await self.async_turn_on()
        # the units never have an active program, the selected one is what runs
        await self._call("select", "select_option", entity_id=self._program,
                         option=PROGRAM + PROGRAMS[hvac_mode])

    async def async_set_temperature(self, **kwargs):
        await self._set_option(SETPOINT, int(kwargs[ATTR_TEMPERATURE]), unit="°C")

    async def async_set_fan_mode(self, fan_mode):
        await self._set_option(FAN, LEVEL + LEVELS[fan_mode])
