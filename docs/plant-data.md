# How the plant-data collector works

The file `src/collect_data/get_plant_data.py` downloads the plant measurements
stored by Deye Cloud and keeps a local CSV up to date. It does not predict
production, calculate a task schedule or control the inverter.

## Where to start reading the code

Start with `get_plant_data()` near the top of `get_plant_data.py`. Its four
numbered steps show the complete CSV workflow. The helpers below it each do one
job: select a plant, choose dates, fetch samples, merge rows, or save a file.

`deye_api.py` contains the connection details: reading `.env`, obtaining the
temporary token, retrying requests and listing accessible plants. You can read
that file separately without losing the thread of how the CSV is updated.

The API still receives the same 30-day windows, with the same request spacing.
Numeric timestamps are converted a column at a time to avoid repeated parsing
of individual readings.

## Run it

From the project folder:

```powershell
python src/collect_data/get_plant_data.py
```

Or call it from another Python file:

```python
from src.collect_data.get_plant_data import get_plant_data

plant_data = get_plant_data()
```

Both use the same default files:

- `.env`: the existing API and account credentials; excluded from Git.
- `data/private/energy.csv`: the actual measurements; also excluded from Git.
- `data/private/energy.metadata.json`: the plant ID, timezone, row count and
  date of the last check. No credentials are stored in this file.

These paths are relative to the project containing the Python file, even when
the terminal is opened in another folder.

## What happens, step by step

1. **Read the local settings.** `deye_api.read_settings()` reads `DEYE_APP_ID`,
   `DEYE_APP_SECRET`, `DEYE_EMAIL` and `DEYE_PASSWORD` from `.env`. Optional
   `DEYE_LOGIN_TYPE` supports mobile or username accounts. An explicit
   environment variable takes priority over the file. Values are never printed.
2. **Log in to Deye's Europe API.** `deye_api.DeyeClient.login()` sends the application
   credentials and account login over HTTPS. Deye requires a SHA-256 form of
   the account password. The response contains a temporary access token, held
   only in memory and used for the monitoring requests.
3. **Select the plant.** The script reads all pages of the plant list. If the
   account has one plant, it selects that plant. Otherwise set `DEYE_STATION_ID`
   in `.env`, or pass `station_id=...`. A business account can also need
   `DEYE_COMPANY_ID` to access its organizationâ€™s plants.
4. **Choose where to start.** If no CSV exists, use the plant's reported
   operating date. If a CSV exists, read its latest `timestamp_utc`, convert it
   to the plant's local date and request that day again. This allows missing
   or corrected readings from the final day to arrive later.
5. **Download the missing period.** The `/station/history/power` endpoint returns
   plant samples. The script requests approximately 30 days at a time, with
   two seconds between API calls. By default it stops at the plant's last
   reported update, so an offline plant does not cause months of empty requests.
6. **Merge by timestamp.** A new timestamp becomes a new CSV row. If Deye sends
   an existing timestamp again, its new non-empty values replace old values.
   An empty value does not erase a previously saved measurement. The resulting
   timestamps are unique and sorted from oldest to newest.
7. **Save after each successful request.** The script writes a temporary CSV
   and then replaces `energy.csv`. If the next API request fails, the previously
   saved chunks remain. Running it again resumes from the last saved day.

For example, if the CSV ends on 2 May at 14:00, the next run starts at local
midnight on 2 May. It can correct that day's readings and append later readings.
Running it twice without new data does not create duplicate rows.

## Understanding the CSV

Each row is one timestamp for one plant. The leading columns are:

- `station_id`: the Deye plant ID.
- `timestamp_utc`: an unambiguous timestamp, useful for joining weather data.
- `timestamp_local`: the same instant in the timezone reported by Deye.

The remaining columns keep Deye's names, including `generationPower`,
`consumptionPower`, `batterySOC`, `batteryPower`, `chargePower`, `dischargePower`
and grid-related fields when returned. The original `timeStamp` is retained too.

The collector does not convert units, turn power into energy, resample readings
to hours, fill gaps or replace blanks with zero. Before training a model, check
the returned fields and units, the actual sampling interval and missing periods.
Some columns are present but empty because Deye does not return them for that
plant or endpoint. A blank is not a measured zero.

The CSV contains the plant-level history exposed to this account. It is not
an export of Deye's whole database or every detailed inverter measurement.

## Reading the update summary

Each run prints the number of CSV rows and the first/last actual reading before
and after the update. Dates are shown in the plant's local timezone, with UTC
offsets. These are measurement dates, not the dates covered by the API request.

It also prints how many rows were genuinely added and the first/last timestamp
among those new rows. Downloading an existing timestamp again, or correcting its
values, does not count as adding a row. New readings that fill an older gap do
count. The reported period spans the first and last added reading; it does not
imply that every intervening interval has data.

If no new timestamps were found, the message is `New rows added: 0`. Existing
readings may still have been refreshed. On the first download, all returned rows
are new. An empty CSV is reported as having no readings yet.

The same information is stored in `energy.metadata.json`: `rows_before`,
`rows_added`, `csv_period_before`, `csv_period_after` and `added_period`.
Each period includes both UTC and local timestamps. An empty period is `null`.

## Optional date ranges

```powershell
python src/collect_data/get_plant_data.py --start 2026-04-01 --end 2026-04-30
```

`--end` includes the whole local date. An explicit `--start` overrides the usual
last-day resume rule, which is useful for checking older gaps or migrated data.
Existing readings outside that range stay in the CSV.

## What happens if something fails?

Temporary network errors, HTTP 429 and server errors are retried with increasing
pauses. Other errors stop the run with a short message. The script checks Deye's
API success flag as well as HTTP status, and rejects a response that claims more
records than it actually contains. It also refuses to merge a CSV belonging to
a different plant or to guess ambiguous local timestamps during clock changes.

Tests cover first-run CSV creation, incremental updates, corrected readings,
missing values, duplicate prevention, date boundaries, wrong-plant protection,
and preserving a saved CSV when an API request fails.
