"""Create or update the local CSV of Deye plant measurements.

Run from the project folder: python -m src.collect_data.get_plant_data
Start with get_plant_data() below to follow the workflow. Connection and login
details are in deye_api.py; the small helpers below handle dates and CSV files.
"""

import argparse
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# Support both "python -m src.collect_data.get_plant_data" and running this file.
if __package__:
    from .deye_api import API_URL, DeyeClient, read_settings
else:
    from deye_api import API_URL, DeyeClient, read_settings


# Always use this project's files, regardless of the terminal's current folder.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CSV_PATH = PROJECT_ROOT / "data/private/energy.csv"


def get_plant_data(csv_path=None, station_id=None, start_date=None, end_date=None,
                   env_file=None, request_interval=2):
    """Create/update the CSV and return its measurements as a DataFrame.

    First run: download from the plant's operating date. Later runs: refresh
    the last saved local day and append newer readings. An explicit start_date
    overrides that rule for a backfill; end_date includes the whole local day.
    Original measurement names, units and missing values are preserved.
    """
    if request_interval < 0:
        raise ValueError("request_interval cannot be negative.")
    path = Path(csv_path) if csv_path is not None else CSV_PATH

    # 1. Read credentials and select the plant. Secrets are never printed.
    settings = read_settings(env_file or PROJECT_ROOT / ".env")
    client = DeyeClient(settings, interval=request_interval)
    station = _select_station(client.stations(), station_id or settings.get("DEYE_STATION_ID"))
    station_id = station["id"]
    if not station.get("regionTimezone"):
        raise ValueError("Deye did not return the plant timezone.")
    zone = ZoneInfo(station["regionTimezone"])

    # 2. Load and validate the existing CSV, then decide what period to fetch.
    data = pd.read_csv(path, dtype={"station_id": str}) if path.exists() else pd.DataFrame()
    data = _merge_samples(data, pd.DataFrame(), station_id, zone)
    # Keep the original timestamps so we can distinguish genuinely new rows
    # from readings that were simply downloaded again or corrected.
    initial_times = pd.DatetimeIndex(data["timestamp_utc"])
    start, stop = _download_period(station, data, start_date, end_date, zone)
    print(f"Plant {station_id}. Dates below use {zone}.", flush=True)
    _print_period("CSV before update", len(data), _describe_period(initial_times, zone))
    print(f"Checking API data from {start.tz_convert(zone).date()}.", flush=True)

    # 3. Download about a month at a time. Save each completed piece so a later
    # network error does not discard the progress already made.
    for window_start, window_stop in _windows(start, stop, zone):
        fresh = _fetch_samples(client, station_id, window_start, window_stop, zone)
        data = _merge_samples(data, fresh, station_id, zone)
        _save_csv(data, path)
        print(f"Checked through {window_stop.tz_convert(zone).isoformat()}: "
              f"{len(fresh):,} returned samples; {len(data):,} CSV rows.", flush=True)

    # 4. Even an empty first download creates a CSV with headers. The metadata
    # records what was checked, but contains no password or access token.
    if not path.exists():
        _save_csv(data, path)
    summary = _save_metadata(path, station_id, zone, data, initial_times)
    if summary["rows_added"]:
        _print_period("New rows added", summary["rows_added"], summary["added_period"])
    else:
        print("New rows added: 0 (no new timestamps found).", flush=True)
    _print_period("CSV after update", len(data), summary["csv_period_after"])
    print(f"Saved {path}.", flush=True)
    return data


def _select_station(stations, requested_id):
    """Use the only accessible plant, or select the explicitly requested one."""
    if requested_id is None:
        if len(stations) != 1:
            raise ValueError("Set DEYE_STATION_ID in .env or pass station_id to select one plant.")
        return stations[0]
    for station in stations:
        if str(station["id"]) == str(requested_id):
            return station
    raise ValueError("Plant not accessible. Check DEYE_STATION_ID and DEYE_COMPANY_ID in .env.")


def _timestamp(value, zone):
    """Interpret an API date string or Unix seconds/milliseconds as UTC."""
    if value is None or value == "":
        raise ValueError("Deye returned a missing timestamp.")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            # Refuse to guess a local time that is ambiguous at a clock change.
            stamp = stamp.tz_localize(zone, ambiguous="raise", nonexistent="raise")
        return stamp.tz_convert("UTC")
    return pd.to_datetime(numeric, unit="ms" if abs(numeric) >= 1e12 else "s", utc=True)


def _local_midnight(day, zone):
    # Construct local midnight first: a day can have 23 or 25 hours when clocks change.
    return pd.Timestamp(datetime.combine(day, time.min, zone)).tz_convert("UTC")


def _download_period(station, data, start_date, end_date, zone):
    """Choose the first local day and the exclusive stopping time in UTC."""
    if start_date is not None:
        first_day = date.fromisoformat(str(start_date))
    elif not data.empty:
        # Refresh the last saved DAY to catch delayed or corrected readings.
        # The timestamp merge prevents these overlapping requests making duplicates.
        first_day = data["timestamp_utc"].max().tz_convert(zone).date()
    else:
        origin = station.get("startOperatingTime") or station.get("createdDate")
        if origin is None:
            raise ValueError("No operating date available. Pass start_date='YYYY-MM-DD'.")
        first_day = _timestamp(origin, zone).tz_convert(zone).date()

    stop = pd.Timestamp.now(tz="UTC").floor("s") + pd.Timedelta(seconds=1)
    if end_date is not None:
        last_day = date.fromisoformat(str(end_date))
        if last_day < first_day:
            raise ValueError("end_date must not be before the requested start date.")
        stop = min(stop, _local_midnight(last_day + timedelta(days=1), zone))
    elif station.get("lastUpdateTime") is not None:
        # An offline plant cannot have new samples after its reported last update.
        stop = min(stop, _timestamp(station["lastUpdateTime"], zone) + pd.Timedelta(seconds=1))
    return _local_midnight(first_day, zone), stop


def _windows(start, stop, zone):
    """Split a period into consecutive 30-day requests, without gaps or overlaps."""
    while start < stop:
        next_day = start.tz_convert(zone).date() + timedelta(days=30)
        following = min(stop, _local_midnight(next_day, zone))
        yield start, following
        start = following


def _fetch_samples(client, station_id, start, stop, zone):
    """Fetch one period and keep every original measurement column."""
    result = client.post("/station/history/power", {
        "stationId": station_id, "startTimestamp": int(start.timestamp()),
        "endTimestamp": int(stop.timestamp()) - 1})
    samples = result.get("stationDataItems")
    if not isinstance(samples, list):
        raise RuntimeError("Unexpected plant-history response; existing CSV was preserved.")
    if result.get("total") is not None and int(result["total"]) > len(samples):
        raise RuntimeError("Deye truncated the history response; existing CSV was preserved.")
    if not samples:
        return pd.DataFrame()

    frame = pd.DataFrame(samples)
    if "timeStamp" not in frame and "dateTime" not in frame:
        raise ValueError("Deye returned a missing timestamp.")
    source_times = frame["timeStamp"] if "timeStamp" in frame else frame["dateTime"]
    if source_times.isna().any() or source_times.eq("").any():
        raise ValueError("Deye returned a missing timestamp.")
    numeric = pd.to_numeric(source_times, errors="coerce")
    # Normal Deye responses use Unix seconds. Convert the whole column in one
    # operation instead of calling the timestamp parser once for every reading.
    if numeric.notna().all() and (numeric.abs() < 1e12).all():
        timestamps = pd.to_datetime(numeric, unit="s", utc=True)
    elif numeric.notna().all() and (numeric.abs() >= 1e12).all():
        timestamps = pd.to_datetime(numeric, unit="ms", utc=True)
    else:
        timestamps = source_times.map(lambda value: _timestamp(value, zone))
    frame["timestamp_utc"] = timestamps
    frame["station_id"] = str(station_id)
    # Enforce our own boundaries if the API includes an extra endpoint sample.
    return frame.loc[(timestamps >= start) & (timestamps < stop)].copy()


def _merge_samples(existing, fresh, station_id, zone):
    """Match timestamps: newer non-null readings win, without erasing saved values."""
    frames = []
    for frame in (existing, fresh):
        frame = frame.copy()
        if frame.empty:
            frames.append(pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC", name="timestamp_utc")))
            continue
        if "timestamp_utc" not in frame or "station_id" not in frame:
            raise ValueError("Existing CSV needs timestamp_utc and station_id columns.")
        if set(frame["station_id"].astype(str)) != {str(station_id)}:
            raise ValueError("This CSV contains another plant; choose a separate CSV path.")
        frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True, errors="raise")
        if frame["timestamp_utc"].isna().any():
            raise ValueError("CSV contains a missing timestamp; existing file was preserved.")
        frame = frame.drop_duplicates("timestamp_utc", keep="last").set_index("timestamp_utc")
        frames.append(frame)
    # combine_first keeps a saved value when the corresponding fresh value is null.
    merged = frames[1].combine_first(frames[0]).sort_index().reset_index()
    merged["station_id"] = str(station_id)
    merged["timestamp_local"] = merged["timestamp_utc"].map(lambda t: t.tz_convert(zone).isoformat())
    columns = ["station_id", "timestamp_utc", "timestamp_local"]
    return merged[columns + [column for column in merged if column not in columns]]


def _save_csv(frame, path):
    """Finish writing the replacement before swapping it with the existing CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    output = frame.copy()
    output["timestamp_utc"] = output["timestamp_utc"].map(lambda t: t.isoformat())
    output.to_csv(temporary, index=False)
    temporary.replace(path)


def _describe_period(timestamps, zone):
    """Describe the first and last actual readings, not the API request dates."""
    if len(timestamps) == 0:
        return None
    first, last = timestamps.min(), timestamps.max()
    return {"start_utc": first.isoformat(), "end_utc": last.isoformat(),
            "start_local": first.tz_convert(zone).isoformat(),
            "end_local": last.tz_convert(zone).isoformat()}


def _print_period(label, rows, period):
    print(f"{label}: {rows:,} rows.", flush=True)
    if period:
        print(f"  From {period['start_local']} to {period['end_local']}.", flush=True)
    else:
        print("  No readings yet.", flush=True)


def _save_metadata(path, station_id, zone, data, initial_times):
    """Write a small record of this update, without credentials."""
    times = data["timestamp_utc"]
    # Compare all timestamps, rather than only checking dates after the old
    # maximum. This also counts new readings that fill an older gap.
    added_times = times.loc[~times.isin(initial_times)]
    metadata = {"station_id": station_id, "timezone": str(zone), "rows": len(data),
                "rows_before": len(initial_times), "rows_added": len(added_times),
                "csv_period_before": _describe_period(initial_times, zone),
                "csv_period_after": _describe_period(times, zone),
                "added_period": _describe_period(added_times, zone),
                "last_sample_utc": None if data.empty else times.max().isoformat(),
                "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "source": API_URL + "/station/history/power",
                "note": "Native API fields and units; no resampling or missing-value filling."}
    sidecar = path.with_suffix(".metadata.json")
    temporary = sidecar.with_suffix(sidecar.suffix + ".tmp")
    temporary.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    temporary.replace(sidecar)
    return metadata


if __name__ == "__main__":
    # Importing this module does not start a download. Only direct execution does.
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
