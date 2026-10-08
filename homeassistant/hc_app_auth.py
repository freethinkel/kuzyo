#!/usr/bin/env python3
"""Logs in to Home Connect as the official app and prints a token blob.

The developer API client HA uses may not set the air conditioner setpoint
(SDK.Error.UnsupportedOption); the app's own OAuth client may. Run this on a
Mac, log in with SingleKey ID in the browser, paste the URL the browser ends
up on. The blob goes to hc_app_token in HA's secrets.yaml.
"""
import base64, hashlib, json, secrets, urllib.parse, urllib.request, webbrowser

CLIENT_ID = "9B75AC9EC512F36C84256AC47D813E2C1DD0D6520DF774B020E1E6E2EB29B1F3"  # from the Android app
REDIRECT = "https://qr.home-connect.com/authorize/prod/"
API = "https://api.home-connect.com/security/oauth"
SCOPES = "Control IdentifyAppliance Monitor Settings WriteAppliance"

verifier = secrets.token_urlsafe(64)
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
query = {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
         "scope": SCOPES, "code_challenge": challenge, "code_challenge_method": "S256"}
url = f"{API}/authorize?{urllib.parse.urlencode(query)}"
print("Opening", url)
webbrowser.open(url)
code = urllib.parse.parse_qs(urllib.parse.urlparse(input("URL after login: ").strip()).query)["code"][0]
body = urllib.parse.urlencode({"grant_type": "authorization_code", "client_id": CLIENT_ID,
                               "redirect_uri": REDIRECT, "code": code, "code_verifier": verifier}).encode()
try:
    token = json.load(urllib.request.urlopen(urllib.request.Request(f"{API}/token", data=body)))
except urllib.error.HTTPError as err:
    raise SystemExit(f"{err.code}: {err.read().decode()}")
print("\nhc_app_token:", base64.b64encode(json.dumps({"client_id": CLIENT_ID, **token}).encode()).decode())
