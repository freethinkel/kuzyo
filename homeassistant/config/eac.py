"""Latest daily kWh reading of the EAC smart meter, for a command_line sensor.

The DSO portal (meterreading-dso.eac.com.cy) has no public API, but its web
app talks JSON: login gives a JWT, service point details list the meter's
measuring channels, readings/list returns {dt, reading, value} per channel. Readings
are daily at midnight and appear a day late. Credentials come from
secrets.yaml: eac_email, eac_password. Run with --channels to list channels.
"""
import datetime, json, sys, urllib.request
import yaml

API = "https://meterreading-dso.eac.com.cy/api/portal/"
secrets = yaml.safe_load(open("/config/secrets.yaml"))


def call(path, body=None, token=None):
    req = urllib.request.Request(API + path, json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


token = call("login", {"email": secrets["eac_email"], "password": secrets["eac_password"]})["jwt"]
sp = call("servicePoints", token=token)[0]
devices = call(f"servicePoints/{sp['id']}", token=token)
meter = next(d for d in devices if not d.get("removalDate"))
channels = [mc for cfg in meter["configurationsList"] for mc in cfg["mcList"]]
if "--channels" in sys.argv:
    print(json.dumps(channels, indent=2))
    sys.exit()

# Daily import register. KWH-30MIN-LP-IMP has half-hour consumption, a day late too.
mc = next(mc for mc in channels if mc["type"] == "S-KWH-24H")
now = datetime.datetime.now(datetime.timezone.utc)
readings = call("readings/list", {"spId": sp["id"], "mcId": mc["id"],
                                  "startDate": (now - datetime.timedelta(days=7)).isoformat(),
                                  "endDate": now.isoformat()}, token)
last = max(readings[0]["readings"], key=lambda r: r["dt"])
print(json.dumps({"reading": last["reading"], "day": last["value"], "dt": last["dt"],
                  "meter": meter["serialNumber"]}))
