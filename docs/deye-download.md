# Downloading Deye Cloud history

The downloader uses Deye's Europe OpenAPI. It reads monitoring data only.
It does not issue inverter commands or change settings.

Use Python 3.10 or newer. On Windows, the `tzdata` package must be installed
if your Python distribution does not already include an IANA timezone database:

```powershell
python -m pip install tzdata
python src/collect_data/get_deye_history.py --station-id YOUR_STATION_ID
```

The program asks for the existing App ID and App Secret from
[Application Credentials](https://developer.deyecloud.com/app), then the
**Deye Cloud account login and password**. The App Secret and account password
are hidden while entered. They are not written to files. Browser sign-in alone
does not authenticate this separate Python process.

If business organizations are listed, choose the organization that owns the
plant. Personal and business tokens have different visibility. A missing plant
can mean the wrong account context, not an empty history database.

For an initial connection check, request one date known to have measurements:

```powershell
python src/collect_data/get_deye_history.py --station-id YOUR_STATION_ID --start 2026-04-01 --end 2026-04-01
```

Then omit the dates to backfill from the plant's reported operating date through
yesterday or its last update date, whichever comes first. For migrated data,
specify `--start YYYY-MM-DD` explicitly if earlier history may exist. The API
does not establish that the reported operating date is the earliest retained
measurement. `--end` is inclusive and uses the plant's local timezone. Use
`--timezone Europe/Andorra` only if the plant timezone is missing or incorrect.
Omit `--station-id` to export all plants in the chosen account context.

## Files

Outputs go to `data/private/deye/` (excluded from Git):

- `raw/`: complete successful monitoring responses, request windows and download
  timestamps. Authentication responses are never archived.
- `csv/<station>/<start>_<end>/station_power.csv`: plant power and battery samples
  at the resolution returned by Deye. Field names and values retain API units.
- `station_daily_energy.csv`: separate daily energy summaries for reconciliation.
  Never treat daily totals as hourly observations.
- `device_measurements.csv`: one row per timestamp and measurement key, including
  the returned name and unit. All available inverter measurement keys are queried.
  Logger and standalone battery histories are not queried separately; battery,
  BMS and generator fields are included when exposed by the inverter.
- `devices.json`: the plant's device inventory.
- `download_summary.json`: record counts, empty windows and failed requests.

No synthetic readings, zero filling, interpolation, resampling or unit conversion
is applied. Samples from the plant and inverter are separate views and must not
be added together. Timestamps with explicit offsets or epochs are converted to
UTC. Naive timestamps are interpreted using the plant timezone; ambiguous or
nonexistent DST times retain the original value, with blank UTC and a status flag.
Review `time_status` before joining data to weather.

## Resume and limitations

Run the same command again after interruption: completed historical responses
are reused. `--refresh` re-fetches cached history, including empty responses.
Date-range CSV folders are rebuilt from cached responses for that requested
range. An interrupted first export may have raw JSON before CSVs are produced.

Requests are spaced two seconds apart by default. Adjust `--interval` to your
application's actual rate limit. Temporary network errors, HTTP 429 and server
errors are retried with backoff. Long backfills can take tens of minutes.
Tokens can be renewed in memory if Deye returns HTTP 401. API-level errors
are recorded rather than treating them as empty data. Exit code 2 means one or
more history requests failed; consult the summary and retry. Empty history is
reported separately and may mean no readings or a retention/access limitation.

The API can return only data retained and authorized for this account. A
successful request is not proof of complete historical coverage. Inspect gaps,
sampling cadence, missing measurements and units before building prediction
models. The first authenticated run is also needed to verify the live device
response format against the documented schema.

Official reference: [Deye OpenAPI specification](https://eu1-developer.deyecloud.com/v2/api-docs),
especially `/account/token`, `/station/history`, `/station/history/power`,
`/device/measurePoints` and `/device/historyRaw` (five-day maximum request window).

Advanced non-interactive authentication can use environment variables
`DEYE_APP_ID`, `DEYE_APP_SECRET`, or an existing `DEYE_ACCESS_TOKEN`. Do not put
credentials in command arguments or commit them to Git. Passwords are always
requested interactively when acquiring a token.
