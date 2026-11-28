"""Create/update data/private/energy.csv from Deye Cloud plant history.

Run: python src/collect_data/get_plant_data.py
Credentials come from the project's Git-ignored .env file. The first run downloads
history from the plant's operating date; subsequent runs refresh the last saved
local day and append new samples. Original API field names and units are retained.
API reference: https://eu1-developer.deyecloud.com/v2/api-docs
"""

from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import time
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pandas as pd


# Build paths from this Python file, not from the terminal's current folder.
# This means the script finds the same .env and CSV when run from VS Code,
# a terminal, or another Python file.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_URL = "https://eu1-developer.deyecloud.com/v1.0"
CSV_PATH = PROJECT_ROOT / "data/private/energy.csv"


def _settings(env_file):
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


class _DeyeClient:
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
        for page in range(1, 10001):
            result = self.post("/station/list", {"page": page, "size": 200})
            items = result.get("stationList")
            if not isinstance(items, list):
                raise RuntimeError("Unexpected station-list response from Deye.")
            if page > 1 and items == previous:
                raise RuntimeError("Deye repeated a station-list page.")
            stations.extend(items)
            if len(items) < 200 or len(stations) >= int(result.get("total", 10**12)):
                return stations
            previous = items
        raise RuntimeError("Too many station-list pages.")


def _timestamp(value, zone):
    """Turn Deye's Unix seconds/milliseconds or date string into an aware UTC time."""
    if value is None or value == "":
        raise ValueError("Deye returned a missing timestamp.")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            # A local clock reading is not always unique when clocks change.
            # Refuse ambiguous readings rather than silently shifting the data.
            stamp = stamp.tz_localize(zone, ambiguous="raise", nonexistent="raise")
        return stamp.tz_convert("UTC")
    return pd.to_datetime(numeric, unit="ms" if abs(numeric) >= 1e12 else "s", utc=True)


def _local_midnight(day, zone):
    # A local day is not always 24 hours because of daylight-saving changes.
    # Construct midnight in the plant's timezone before converting it to UTC.
    return pd.Timestamp(datetime.combine(day, day_time.min, zone)).tz_convert("UTC")


def _merge_samples(existing, fresh, station_id, zone):
    """A newer non-null reading wins; an API null cannot erase saved data."""
    frames = []
    for frame in (existing, fresh):
        frame = frame.copy()
        if frame.empty:
            frames.append(pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC", name="timestamp_utc")))
            continue
        if "timestamp_utc" not in frame or "station_id" not in frame:
            raise ValueError("Existing CSV needs timestamp_utc and station_id columns.")
        # Each CSV belongs to one plant. Mixing two plants would create a
        # misleading training dataset with overlapping timestamps.
        if set(frame["station_id"].astype(str)) != {str(station_id)}:
            raise ValueError("This CSV contains another plant; choose a separate CSV path.")
        frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="raise")
        if frame["timestamp_utc"].isna().any():
            raise ValueError("CSV contains a missing timestamp; existing file was preserved.")
        frame = frame.drop_duplicates("timestamp_utc", keep="last").set_index("timestamp_utc")
        frames.append(frame)
    # Put timestamps on the index so equal timestamps match. Fresh non-null
    # values replace old ones, and old values survive if Deye now returns null.
    # This both appends new samples and corrects late updates without duplicates.
    merged = frames[1].combine_first(frames[0]).sort_index().reset_index()
    merged["station_id"] = str(station_id)
    merged["timestamp_local"] = merged["timestamp_utc"].map(lambda t: t.tz_convert(zone).isoformat())
    columns = ["station_id", "timestamp_utc", "timestamp_local"]
    return merged[columns + [column for column in merged if column not in columns]]


def _save_csv(frame, path):
    # Write the replacement completely before swapping it into place. If a
    # request or write fails, the previous completed CSV remains available.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    output = frame.copy()
    output["timestamp_utc"] = output["timestamp_utc"].map(lambda t: t.isoformat())
    output.to_csv(temporary, index=False)
    temporary.replace(path)


def get_plant_data(csv_path=None, station_id=None, start_date=None, end_date=None,
                   env_file=None, request_interval=2):
    """Return a DataFrame and create/update its CSV with native Deye samples.

    With no CSV, start at the plant's reported operating date (or start_date).
    Otherwise resume from midnight of the CSV's latest local date. Optional
    start_date overrides this for an explicit backfill; end_date is inclusive.
    No data is interpolated, converted to other units, or replaced with zeros.
    Each completed request is saved atomically so an interrupted run can resume.
    """
    path = Path(csv_path) if csv_path is not None else CSV_PATH
    if request_interval < 0:
        raise ValueError("request_interval cannot be negative.")
    settings = _settings(env_file or PROJECT_ROOT / ".env")
    requested_id = station_id or settings.get("DEYE_STATION_ID")
    client = _DeyeClient(settings, interval=request_interval)
    stations = client.stations()
    # With only one accessible plant, no extra configuration is needed.
    # Multiple plants require an explicit ID so we do not download the wrong one.
    if requested_id is None:
        if len(stations) != 1:
            raise ValueError("Set DEYE_STATION_ID in .env or pass station_id to select one plant.")
        station = stations[0]
    else:
        station = next((item for item in stations if str(item["id"]) == str(requested_id)), None)
        if station is None:
            raise ValueError("Plant not accessible. Check DEYE_STATION_ID and DEYE_COMPANY_ID in .env.")
    station_id = station["id"]
    if not station.get("regionTimezone"):
        raise ValueError("Deye did not return the plant timezone.")
    zone = ZoneInfo(station["regionTimezone"])
    # FIRST RUN: the file does not exist yet, so begin with an empty table.
    # LATER RUNS: load the same CSV and use its latest timestamp as our starting
    # point. Keep plant IDs as text so CSV reading cannot change their format.
    existing = pd.read_csv(path, dtype={"station_id": str}) if path.exists() else pd.DataFrame()
    # Validate an existing file before using its dates or changing it.
    existing = _merge_samples(existing, pd.DataFrame(), station_id, zone)
    initial_rows = len(existing)
    if start_date is not None:
        first_day = date.fromisoformat(str(start_date))
    elif not existing.empty:
        # Re-read the last saved LOCAL DAY, rather than starting one second
        # after the last row. Deye may upload missing or corrected readings late.
        # The merge below prevents these overlapping requests making duplicates.
        first_day = existing["timestamp_utc"].max().tz_convert(zone).date()
    else:
        origin = station.get("startOperatingTime") or station.get("createdDate")
        if origin is None:
            raise ValueError("No operating date available. Pass start_date='YYYY-MM-DD'.")
        first_day = _timestamp(origin, zone).tz_convert(zone).date()
    cursor = _local_midnight(first_day, zone)
    stop = pd.Timestamp.now(tz="UTC").floor("s") + pd.Timedelta(seconds=1)
    if end_date is not None:
        last_day = date.fromisoformat(str(end_date))
        if last_day < first_day:
            raise ValueError("end_date must not be before the requested start date.")
        stop = min(stop, _local_midnight(last_day + timedelta(days=1), zone))
    elif station.get("lastUpdateTime") is not None:
        # Do not request months after an offline plant's last reported update.
        stop = min(stop, _timestamp(station["lastUpdateTime"], zone) + pd.Timedelta(seconds=1))
    print(f"Plant {station_id}: {initial_rows:,} existing rows. Starting {first_day}.", flush=True)
    # Download in roughly monthly pieces. That is much faster than one request
    # per day, while keeping responses reasonably small. Save after each piece
    # so the next run can continue if the connection drops halfway through.
    while cursor < stop:
        next_day = cursor.tz_convert(zone).date() + timedelta(days=30)
        following = min(stop, _local_midnight(next_day, zone))
        result = client.post("/station/history/power", {
            "stationId": station_id, "startTimestamp": int(cursor.timestamp()),
            "endTimestamp": int(following.timestamp()) - 1})
        samples = result.get("stationDataItems")
        if not isinstance(samples, list):
            raise RuntimeError("Unexpected plant-history response; existing CSV was preserved.")
        if result.get("total") is not None and int(result["total"]) > len(samples):
            raise RuntimeError("Deye truncated the history response; existing CSV was preserved.")
        rows = []
        for sample in samples:
            stamp = _timestamp(sample.get("timeStamp", sample.get("dateTime")), zone)
            # Enforce our own date boundaries, even if Deye includes an extra
            # endpoint sample. Keep every original measurement column unchanged.
            if cursor <= stamp < following:
                rows.append({**sample, "station_id": str(station_id), "timestamp_utc": stamp})
        existing = _merge_samples(existing, pd.DataFrame(rows), station_id, zone)
        _save_csv(existing, path)
        print(f"Checked through {following.tz_convert(zone).isoformat()}: "
              f"{len(rows):,} returned samples; {len(existing):,} CSV rows.", flush=True)
        cursor = following
    # Also create a header-only file if the API has no historical samples.
    if not path.exists():
        _save_csv(existing, path)
    # This small companion file explains where the CSV came from and when it
    # was checked. It contains no App Secret, password or access token.
    metadata = {"station_id": station_id, "timezone": str(zone), "rows": len(existing),
                "rows_added": len(existing) - initial_rows,
                "last_sample_utc": None if existing.empty else existing["timestamp_utc"].max().isoformat(),
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "source": API_URL + "/station/history/power",
                "note": "Native API fields and units; no resampling or missing-value filling."}
    sidecar = path.with_suffix(".metadata.json")
    temporary = sidecar.with_suffix(sidecar.suffix + ".tmp")
    temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    temporary.replace(sidecar)
    print(f"Saved {path} (+{metadata['rows_added']:,} rows).", flush=True)
    return existing


if __name__ == "__main__":
    # This block runs only when the file is executed directly. Other Python
    # files can import get_plant_data() without triggering an API download.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--station-id", type=int)
    parser.add_argument("--start", help="First local date, YYYY-MM-DD")
    parser.add_argument("--end", help="Last local date, YYYY-MM-DD (inclusive)")
    parser.add_argument("--env-file", type=Path, default=PROJECT_ROOT / ".env")
    arguments = parser.parse_args()
    try:
        get_plant_data(arguments.csv, arguments.station_id, arguments.start,
                       arguments.end, arguments.env_file)
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, f"Download stopped: {error}\n")
