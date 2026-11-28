"""Deye Cloud connection: local settings, authentication and reliable API reads.

The CSV workflow lives in get_plant_data.py. This file handles only communication
with Deye; it does not save measurements or change inverter settings.
"""

import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

API_URL = "https://eu1-developer.deyecloud.com/v1.0"


def read_settings(env_file):
    """Read literal .env assignments without printing or executing their values."""
    settings = {}
    path = Path(env_file)
    if path.exists():
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError(f"Invalid .env assignment on line {number}.")
            key, value = (part.strip() for part in line.split("=", 1))
            if not key.startswith("DEYE_"):
                continue
            # Quotes are optional. Do not interpret $variables, backslashes or
            # inline # characters: they may be part of the actual password.
            if value.startswith(('"', "'")):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError(f"Unclosed .env quote on line {number}.")
                value = value[1:-1]
            if value:
                settings[key] = value
    # An explicitly configured environment variable takes priority over .env.
    settings.update({key: value for key, value in os.environ.items()
                     if key.startswith("DEYE_") and value})
    return settings


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward a login request or access token to another address.
        return None


class DeyeClient:
    """Handle login and the small number of API calls needed by this collector."""
    def __init__(self, settings, interval=2):
        self.settings = settings
        self.interval = interval
        self.last_request = 0.0
        self.opener = build_opener(_NoRedirect())
        self.token = settings.get("DEYE_ACCESS_TOKEN", "")
        if not self.token:
            self.login()

    def login(self):
        # Deye needs two kinds of credentials:
        #   1. App ID + App Secret identify the developer application.
        #   2. The account login + password determine which plants it can read.
        # We exchange them for a temporary token; we never save that token.
        required = [key for key in ("DEYE_APP_ID", "DEYE_APP_SECRET", "DEYE_PASSWORD")
                    if not self.settings.get(key)]
        if required:
            raise ValueError("Fill these fields in .env: " + ", ".join(required))
        app_id = self.settings["DEYE_APP_ID"]
        if not app_id.isdigit():
            raise ValueError("DEYE_APP_ID must contain digits only.")
        kind = self.settings.get("DEYE_LOGIN_TYPE", "email")
        if kind not in {"email", "mobile", "username"}:
            raise ValueError("DEYE_LOGIN_TYPE must be email, mobile or username.")
        login_key = "DEYE_" + kind.upper()
        if not self.settings.get(login_key):
            raise ValueError(f"Fill {login_key} in .env.")
        body = {"appSecret": self.settings["DEYE_APP_SECRET"],
                kind: self.settings[login_key],
                # This SHA-256 format is required by Deye's token endpoint.
                # The request itself still travels over HTTPS.
                "password": hashlib.sha256(self.settings["DEYE_PASSWORD"].encode()).hexdigest()}
        if kind == "mobile":
            if not self.settings.get("DEYE_COUNTRY_CODE"):
                raise ValueError("Fill DEYE_COUNTRY_CODE in .env for a mobile login.")
            body["countryCode"] = self.settings["DEYE_COUNTRY_CODE"]
        if self.settings.get("DEYE_COMPANY_ID"):
            body["companyId"] = int(self.settings["DEYE_COMPANY_ID"])
        result = self.post("/account/token?appId=" + app_id, body, authenticated=False)
        self.token = result.get("accessToken", "")
        if not self.token:
            raise RuntimeError("Deye did not return an access token.")

    def post(self, endpoint, body, *, authenticated=True, renew=True):
        # Deye's read endpoints use HTTP POST with a JSON body. Here, POST
        # retrieves monitoring history; it does not change inverter settings.
        for attempt in range(5):
            # Space requests out instead of sending a burst of API calls.
            time.sleep(max(0, self.last_request + self.interval - time.monotonic()))
            headers = {"Content-Type": "application/json"}
            if authenticated:
                headers["Authorization"] = "Bearer " + self.token
            self.last_request = time.monotonic()
            request = Request(API_URL + endpoint, json.dumps(body).encode(), headers)
            try:
                with self.opener.open(request, timeout=90) as response:
                    result = json.load(response)
            except HTTPError as error:
                # A long initial download may outlive its token. Renew it once
                # and retry this request, rather than starting the export over.
                if error.code == 401 and authenticated and renew and self.settings.get("DEYE_PASSWORD"):
                    self.login()
                    return self.post(endpoint, body, renew=False)
                if error.code in {429, 500, 502, 503, 504} and attempt < 4:
                    # 429 means "too many requests". Respect Deye's requested
                    # pause when given; otherwise use progressively longer waits.
                    retry_after = error.headers.get("Retry-After", "0")
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = 0
                    time.sleep(max(2 ** (attempt + 1), delay))
                    continue
                raise RuntimeError(f"Deye {endpoint.split('?')[0]}: HTTP {error.code}.") from None
            except (URLError, TimeoutError, OSError):
                if attempt == 4:
                    raise RuntimeError("Could not connect to Deye; existing CSV was preserved.") from None
                time.sleep(2 ** (attempt + 1))
                continue
            except ValueError:
                raise RuntimeError("Deye returned an invalid JSON response.") from None
            # HTTP 200 alone is not enough: Deye may put an API error in a
            # successful HTTP response. Do not mistake that for an empty day.
            if not isinstance(result, dict) or result.get("success") is not True:
                code = result.get("code", "unknown") if isinstance(result, dict) else "unknown"
                raise RuntimeError(f"Deye {endpoint.split('?')[0]}: API code {code}.")
            return result
        raise RuntimeError("Deye retry limit reached.")

    def stations(self):
        # The API returns plant lists in pages. Read all pages so a selected
        # plant is not missed just because it was absent from the first page.
        stations = []
        # There is no previous page before the first request.
        previous = None
        for page in range(1, 10001):
            result = self.post("/station/list", {"page": page, "size": 200})
            items = result.get("stationList")
            if not isinstance(items, list):
                raise RuntimeError("Unexpected station-list response from Deye.")
            if previous is not None and items == previous:
                raise RuntimeError("Deye repeated a station-list page.")
            stations.extend(items)
            if len(items) < 200 or len(stations) >= int(result.get("total", 10**12)):
                return stations
            previous = items
        raise RuntimeError("Too many station-list pages.")

