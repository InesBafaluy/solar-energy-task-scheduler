"""Read Deye's Europe OpenAPI and archive history locally. Python 3.10+."""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, time as dt_time, timedelta, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, HTTPRedirectHandler, Request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BASE_URL = "https://eu1-developer.deyecloud.com/v1.0"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / "data/private/deye"
READ_ENDPOINTS = {
    "/account/info", "/station/list", "/station/device", "/station/history",
    "/station/history/power", "/device/measurePoints", "/device/historyRaw",
}


class DeyeError(RuntimeError):
    """An API failure without credential or response-body disclosure."""


class AuthenticationError(DeyeError):
    """Stop the backfill when authentication or authorization is denied."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward credentials to a redirect destination.


class Client:
    def __init__(self, interval=2.0):
        self.interval = interval
        self.token = os.environ.get("DEYE_ACCESS_TOKEN", "")
        self.credentials = None
        self.last_request = 0.0
        self.opener = build_opener(NoRedirect())

    def post(self, endpoint, body=None, *, auth=True, renew=True):
        if auth and endpoint not in READ_ENDPOINTS:
            raise DeyeError("This downloader only supports monitoring endpoints.")
        if not auth and not endpoint.startswith("/account/token?appId="):
            raise DeyeError("Unsupported authentication endpoint.")
        for attempt in range(5):
            time.sleep(max(0, self.last_request + self.interval - time.monotonic()))
            headers = {"Content-Type": "application/json"}
            if auth:
                headers["Authorization"] = "Bearer " + self.token
            request = Request(BASE_URL + endpoint, json.dumps(body or {}).encode(), headers)
            self.last_request = time.monotonic()
            try:
                with self.opener.open(request, timeout=60) as response:
                    result = json.load(response)
            except HTTPError as exc:
                if exc.code == 401 and auth and renew and self.credentials:
                    self.login()
                    return self.post(endpoint, body, renew=False)
                if exc.code in {401, 403}:
                    raise AuthenticationError(f"{endpoint.split('?')[0]}: HTTP {exc.code}; check credentials and monitoring permissions") from None
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 4:
                    raise DeyeError(f"{endpoint.split('?')[0]}: HTTP {exc.code}") from None
                retry = exc.headers.get("Retry-After", "0")
                try:
                    delay = float(retry)
                except ValueError:
                    try:
                        from email.utils import parsedate_to_datetime
                        delay = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                    except (ValueError, TypeError):
                        delay = 0
                time.sleep(max(2 ** (attempt + 1), delay))
                continue
            except (URLError, TimeoutError, OSError, ValueError):
                if attempt == 4:
                    raise DeyeError(f"{endpoint.split('?')[0]}: network or JSON error") from None
                time.sleep(2 ** (attempt + 1))
                continue
            if not isinstance(result, dict) or result.get("success") is not True:
                # Do not echo API messages, which can contain submitted identifiers.
                code = result.get("code", "unknown") if isinstance(result, dict) else "invalid response"
                raise DeyeError(f"{endpoint.split('?')[0]}: API code {code}")
            return result
        raise DeyeError("Retry limit reached")

    def login(self):
        if self.credentials is None:
            app_id = os.environ.get("DEYE_APP_ID") or input("App ID (from developer portal): ").strip()
            secret = os.environ.get("DEYE_APP_SECRET") or getpass.getpass("App Secret (hidden): ")
            kind = (input("Login type [email / mobile / username; default email]: ").strip() or "email")
            if kind not in {"email", "mobile", "username"}:
                raise DeyeError("Choose email, mobile or username.")
            account = input(f"Deye Cloud {kind}: ").strip()
            password = getpass.getpass("Deye Cloud account password (hidden): ")
            if not app_id.isdigit() or not secret or not account or not password:
                raise DeyeError("An App ID, App Secret, login and password are required.")
            body = {"appSecret": secret, kind: account,
                    "password": hashlib.sha256(password.encode()).hexdigest()}
            del password
            if kind == "mobile":
                body["countryCode"] = input("Country calling code, without +: ").strip()
            self.credentials = (app_id, body)
        app_id, body = self.credentials
        result = self.post("/account/token?appId=" + app_id, body, auth=False)
        if not result.get("accessToken"):
            raise DeyeError("Deye did not return an access token.")
        self.token = result["accessToken"]

    def choose_account(self, company_id=None):
        if not self.token:
            self.login()
        if company_id is None:
            organizations = self.post("/account/info").get("orgInfoList", [])
            if organizations:
                print("\nBusiness accounts available (the web plant may belong to one):")
                print("0: Personal account")
                for i, item in enumerate(organizations, 1):
                    print(f"{i}: {item.get('companyName', '')} ({item['companyId']})")
                choice = input("Account number [0]: ").strip() or "0"
                if not choice.isdigit() or not 0 <= int(choice) <= len(organizations):
                    raise DeyeError("Invalid account number.")
                if int(choice):
                    company_id = organizations[int(choice) - 1]["companyId"]
        if company_id is not None:
            if self.credentials is None:
                self.login()
            self.credentials[1]["companyId"] = company_id
            self.login()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class Archive:
    def __init__(self, client, root, refresh=False):
        self.client, self.root, self.refresh = client, Path(root), refresh

    def get(self, endpoint, body, *, cache=True):
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:20]
        path = self.root / "raw" / endpoint.strip("/").replace("/", "_") / (digest + ".json")
        if cache and not self.refresh and path.exists():
            envelope = json.loads(path.read_text(encoding="utf-8"))
            return envelope["response"]
        result = self.client.post(endpoint, body)
        # Authentication requests and tokens are never archived.
        atomic_json(path, {"endpoint": endpoint, "request": body,
                           "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
                           "response": result})
        return result


def paginated(archive, endpoint, body, key):
    values = []
    for page in range(1, 10001):
        result = archive.get(endpoint, {**body, "page": page, "size": 200}, cache=False)
        items = result.get(key)
        if not isinstance(items, list):
            raise DeyeError(f"{endpoint}: unexpected list response ({key})")
        if not items:
            return values
        if page > 1 and items == previous:
            raise DeyeError(f"{endpoint}: server repeated a page; refusing incomplete export")
        values.extend(items)
        if (result.get("total") is not None and len(values) >= int(result["total"])) or len(items) < 200:
            return values
        previous = items
    raise DeyeError("Too many API pages")


def dates(start, end, days):
    """Yield half-open date windows; CLI end date itself is inclusive."""
    stop = end + timedelta(days=1)
    while start < stop:
        following = min(start + timedelta(days=days), stop)
        yield start, following
        start = following


def parse_time(value, zone):
    """Keep ambiguous local times out of UTC rather than guessing a DST fold."""
    if value in (None, ""):
        return "", "missing"
    try:
        if isinstance(value, (float, int)) or re.fullmatch(r"\d{10}(?:\d{3})?(?:\.\d+)?", str(value)):
            seconds = float(value)
            if seconds >= 1e12:
                seconds /= 1000
            return datetime.fromtimestamp(seconds, timezone.utc).isoformat(), "ok"
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00").replace("/", "-"))
        if stamp.tzinfo is None:
            candidates = [stamp.replace(tzinfo=zone, fold=i) for i in (0, 1)]
            valid = [c for c in candidates if c.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == stamp]
            if not valid:
                return "", "nonexistent_local_time"
            if len({c.utcoffset() for c in valid}) > 1:
                return "", "ambiguous_local_time"
            stamp = valid[0]
        return stamp.astimezone(timezone.utc).isoformat(), "ok"
    except (ValueError, TypeError, OSError, OverflowError):
        return "", "unparsed"


def csv_file(path, rows, minimum_fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys([*minimum_fields, *(key for row in rows for key in row)]))
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                             for key, value in row.items()})
    temporary.replace(path)


def export_station(archive, station, start, end, zone, *, device_sn=None):
    station_id = station["id"]
    prefix = archive.root / "csv" / str(station_id) / f"{start}_{end}"
    failures, empty, power, daily, measurements = [], [], [], [], []

    def fetch(endpoint, body, key):
        try:
            result = archive.get(endpoint, body)
            items = result.get(key)
            if not isinstance(items, list):
                raise DeyeError(f"{endpoint}: unexpected response ({key})")
            total = result.get("total")
            if total is not None and int(total) > len(items):
                raise DeyeError(f"{endpoint}: response appears truncated")
            if not items:
                empty.append({"endpoint": endpoint, "request": body})
            return items
        except AuthenticationError:
            raise
        except DeyeError as exc:
            failures.append({"endpoint": endpoint, "request": body, "error": str(exc)})
            return []

    # Retain native sample resolution. Never replace missing readings with zero.
    for a, b in dates(start, end, 1):
        body = {"stationId": station_id,
                "startTimestamp": int(datetime.combine(a, dt_time.min, zone).timestamp()),
                "endTimestamp": int(datetime.combine(b, dt_time.min, zone).timestamp()) - 1}
        for item in fetch("/station/history/power", body, "stationDataItems"):
            source_time = item.get("timeStamp", item.get("dateTime"))
            utc, status = parse_time(source_time, zone)
            power.append({**item, "station_id": station_id, "timestamp_source": source_time,
                          "timestamp_utc": utc, "timezone": str(zone), "time_status": status})
        print(f"Plant power: {a}", flush=True)

    # Deye documents station daily endAt as excluded (up to 31 days/request).
    for a, b in dates(start, end, 31):
        body = {"stationId": station_id, "granularity": 2,
                "startAt": a.isoformat(), "endAt": b.isoformat()}
        for item in fetch("/station/history", body, "stationDataItems"):
            daily.append({**item, "station_id": station_id, "timezone": str(zone)})

    devices = paginated(archive, "/station/device", {"stationIds": [station_id]}, "deviceListItems")
    atomic_json(prefix / "devices.json", devices)
    if device_sn and not any(d.get("deviceSn") == device_sn for d in devices):
        raise DeyeError("The requested device is not in the selected plant.")
    for device in devices:
        if device.get("deviceType") not in {"INVERTER", "MICRO_INVERTER", "MICRO_STORAGE_IN_ONE", "PCS"}:
            continue
        sn = device["deviceSn"]
        if device_sn and sn != device_sn:
            continue
        identity = {"deviceSn": sn, "deviceType": device["deviceType"]}
        points = fetch("/device/measurePoints", identity, "measurePoints")
        if not points:
            continue
        if not all(isinstance(p, str) for p in points):
            raise DeyeError("Unexpected measure-point format; raw response preserved.")
        # One local day is comfortably within the documented five-day window.
        for a, b in dates(start, end, 1):
            body = {**identity, "measurePoints": points,
                    "startTimestamp": int(datetime.combine(a, dt_time.min, zone).timestamp()),
                    "endTimestamp": int(datetime.combine(b, dt_time.min, zone).timestamp()) - 1}
            for sample in fetch("/device/historyRaw", body, "dataList"):
                source_time = sample.get("time", sample.get("collectionTime"))
                utc, status = parse_time(source_time, zone)
                if not isinstance(sample.get("itemList"), list):
                    failures.append({"endpoint": "/device/historyRaw", "request": body,
                                     "error": "Unexpected sample itemList; raw response preserved"})
                    continue
                for item in sample["itemList"]:
                    measurements.append({**item, "station_id": station_id, "device_sn": sn,
                                         "device_type": device["deviceType"], "timestamp_source": source_time,
                                         "timestamp_utc": utc, "timezone": str(zone), "time_status": status})
            print(f"Device {sn}: {a}", flush=True)

    # Inclusive timestamp edges may be returned twice; remove exact duplicates only.
    def unique(rows):
        seen = set()
        for row in rows:
            identity = json.dumps(row, sort_keys=True, ensure_ascii=False)
            if identity not in seen:
                seen.add(identity)
                yield row

    power = list(unique(power))
    daily = list(unique(daily))
    measurements = list(unique(measurements))
    csv_file(prefix / "station_power.csv", power, ["station_id", "timestamp_source", "timestamp_utc", "timezone", "time_status"])
    csv_file(prefix / "station_daily_energy.csv", daily, ["station_id", "year", "month", "day", "timezone"])
    csv_file(prefix / "device_measurements.csv", measurements,
             ["station_id", "device_sn", "device_type", "timestamp_source", "timestamp_utc", "timezone", "time_status", "key", "value", "unit", "name"])
    summary = {"station_id": station_id, "start_date": str(start), "end_date_inclusive": str(end),
               "timezone": str(zone), "station_power_rows": len(power), "station_daily_rows": len(daily),
               "device_measurement_rows": len(measurements), "empty_requests": empty, "failures": failures,
               "finished_at_utc": datetime.now(timezone.utc).isoformat(),
               "note": "Counts are returned records, not a guarantee of complete history. Original API units and field names are retained."}
    atomic_json(prefix / "download_summary.json", summary)
    print(f"\nSaved {len(power)} plant samples, {len(daily)} daily records, "
          f"{len(measurements)} device measurements to {prefix}")
    print(f"Empty requests: {len(empty)}; failed requests: {len(failures)}")
    if not power and not measurements:
        print("No detailed history returned. Daily totals alone cannot train an hourly forecasting model.")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station-id", type=int, help="Download only this authorized plant")
    parser.add_argument("--device-sn", help="Download device measurements only for this device")
    parser.add_argument("--company-id", type=int, help="Business organization ID, if known")
    parser.add_argument("--start", type=date.fromisoformat, help="First local date, YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, help="Last local date (inclusive), default yesterday")
    parser.add_argument("--timezone", help="Override plant IANA timezone, e.g. Europe/Andorra")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--refresh", action="store_true", help="Re-fetch even cached history (including empty results)")
    parser.add_argument("--interval", type=float, default=2, help="Seconds between requests, default 2")
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.start and args.end and args.start > args.end:
        parser.error("--start must be on or before --end")
    client = Client(args.interval)
    client.choose_account(args.company_id)
    archive = Archive(client, args.output, args.refresh)
    stations = paginated(archive, "/station/list", {}, "stationList")
    if args.station_id is not None:
        stations = [s for s in stations if s["id"] == args.station_id]
    if not stations:
        raise DeyeError("No matching plants. Check the personal/business account choice and region.")
    any_failures = False
    for station in stations:
        zone_name = args.timezone or station.get("regionTimezone")
        if not zone_name:
            raise DeyeError("Plant timezone missing. Supply --timezone Europe/Andorra (or the correct plant zone).")
        try:
            zone = ZoneInfo(zone_name)
        except ZoneInfoNotFoundError:
            raise DeyeError("Timezone unavailable. Install Python's tzdata package and check --timezone.") from None
        end = args.end or (datetime.now(zone).date() - timedelta(days=1))
        if args.end is None:
            last_update, status = parse_time(station.get("lastUpdateTime"), zone)
            if status == "ok":
                end = min(end, datetime.fromisoformat(last_update).astimezone(zone).date())
        start = args.start
        if start is None:
            source = station.get("startOperatingTime") or station.get("createdDate")
            utc, status = parse_time(source, zone)
            if status != "ok":
                raise DeyeError("No usable plant start date. Supply --start YYYY-MM-DD.")
            start = datetime.fromisoformat(utc).astimezone(zone).date()
        if start > end:
            raise DeyeError("Plant start date is after the requested end date.")
        print(f"\n{station.get('name', station['id'])}: {start} through {end} ({zone})", flush=True)
        print("Historical responses are cached; credentials are used only in memory.")
        print(f"Detailed history uses about two requests per day per inverter plus metadata; "
              f"at {args.interval:g} seconds/request a long backfill can take a while.", flush=True)
        summary = export_station(archive, station, start, end, zone, device_sn=args.device_sn)
        any_failures |= bool(summary["failures"])
    return 2 if any_failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped. Re-run the same command to resume from cached responses.")
        sys.exit(130)
    except (DeyeError, OSError, ValueError) as exc:
        print(f"Download stopped: {exc}", file=sys.stderr)
        sys.exit(1)
