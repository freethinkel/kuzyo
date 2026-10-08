"""Half-hour consumption from the EAC smart meter, for the Energy dashboard.

Same portal API as eac.py. HA takes past data with its own timestamps only
as external statistics, so this imports eac:grid_consumption: hourly sums of
the KWH-30MIN-LP-IMP channel, whose dt is local time at the start of each
half hour. The portal publishes them a few hours late and returns at most
1000 points per request, so it is read in two-week windows.

HA cannot price external statistics itself, so eac:grid_cost is imported
alongside at the configured all-in price per kWh. Each hour is priced when it
is imported: the first import priced all history at the price of that day.
"""
from datetime import datetime, timedelta
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData, StatisticMeanType, StatisticMetaData)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics, get_last_statistics)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

DOMAIN = "eac"
API = "https://meterreading-dso.eac.com.cy/api/portal/"
STAT = "eac:grid_consumption"
COST = "eac:grid_cost"
META = {
    STAT: StatisticMetaData(
        statistic_id=STAT, source=DOMAIN, name="EAC grid consumption",
        unit_of_measurement="kWh", unit_class="energy",
        has_sum=True, mean_type=StatisticMeanType.NONE),
    COST: StatisticMetaData(
        statistic_id=COST, source=DOMAIN, name="EAC grid cost",
        unit_of_measurement="EUR", unit_class=None,
        has_sum=True, mean_type=StatisticMeanType.NONE),
}
BACKFILL = timedelta(days=365)
WINDOW = timedelta(days=14)
_LOGGER = logging.getLogger(__name__)


async def async_setup(hass, config):
    conf = config[DOMAIN]
    session = async_get_clientsession(hass)

    async def call(path, body=None, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with session.request("POST" if body is not None else "GET", API + path,
                                   json=body, headers=headers, timeout=60) as r:
            r.raise_for_status()
            return await r.json()

    async def last(stat):
        rows = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, stat, True, {"sum"})
        if rows:
            return dt_util.utc_from_timestamp(rows[stat][0]["start"]) + timedelta(hours=1), rows[stat][0]["sum"]
        return dt_util.utcnow() - BACKFILL, 0.0

    async def update(_now=None):
        state = {stat: list(await last(stat)) for stat in META}
        price = {STAT: 1.0, COST: conf["price"]}
        since = min(s for s, _ in state.values())

        token = (await call("login", {"email": conf["email"], "password": conf["password"]}))["jwt"]
        sp = (await call("servicePoints", token=token))[0]["id"]
        meter = next(d for d in await call(f"servicePoints/{sp}", token=token)
                     if not d.get("removalDate"))
        mc = next(mc for cfg in meter["configurationsList"] for mc in cfg["mcList"]
                  if mc["type"] == "KWH-30MIN-LP-IMP")["id"]

        points = {}
        start, now = since - timedelta(days=1), dt_util.utcnow()
        while start < now:
            end = min(start + WINDOW, now)
            body = {"spId": sp, "mcId": mc, "startDate": start.isoformat(), "endDate": end.isoformat()}
            for rd in (await call("readings/list", body, token))[0]["readings"]:
                points[rd["dt"]] = rd["value"]
            start = end

        hours = {}
        tz = dt_util.get_default_time_zone()
        for dt, value in points.items():
            hour = datetime.fromisoformat(dt).replace(minute=0, tzinfo=tz)
            hours.setdefault(hour, []).append(value)

        stats = {stat: [] for stat in META}
        for hour in sorted(h for h in hours if h >= since):
            if len(hours[hour]) < 2 and hour == max(hours):
                break  # the newest half hour is not published yet; older gaps are for good
            for stat, (stat_since, total) in state.items():
                if hour >= stat_since:
                    value = sum(hours[hour]) * price[stat]
                    state[stat][1] = total = total + value
                    stats[stat].append(StatisticData(start=hour, state=value, sum=total))
        for stat, rows in stats.items():
            if rows:
                async_add_external_statistics(hass, META[stat], rows)
        _LOGGER.debug("Imported %s from %s", {s: len(r) for s, r in stats.items()}, since)

    async def safe_update(_now=None):
        try:
            await update()
        except Exception:
            _LOGGER.exception("EAC import failed")

    async_at_started(hass, safe_update)
    async_track_time_interval(hass, safe_update, timedelta(hours=3))
    return True
