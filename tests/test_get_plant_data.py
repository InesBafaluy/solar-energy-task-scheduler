import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

import pandas as pd

MODULE = Path(__file__).resolve().parents[1] / "src/collect_data/get_plant_data.py"
sys.path.insert(0, str(MODULE.parent))
spec = importlib.util.spec_from_file_location("plant_data", MODULE)
plant = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plant)


class FakeClient:
    samples = []
    calls = []

    def __init__(self, settings, interval):
        pass

    def stations(self):
        return [{"id": 1, "regionTimezone": "Europe/Andorra",
                 "startOperatingTime": "2026-04-01T00:00:00+02:00",
                 "lastUpdateTime": "2026-04-03T18:00:00+02:00"}]

    def post(self, endpoint, body):
        self.calls.append(body)
        return {"success": True, "total": len(self.samples), "stationDataItems": self.samples}


def sample(instant, generation, consumption=50):
    return {"timeStamp": pd.Timestamp(instant).timestamp(),
            "generationPower": generation, "consumptionPower": consumption}


class PlantDataTests(unittest.TestCase):
    def setUp(self):
        FakeClient.calls = []
        FakeClient.samples = []
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root / "private/energy.csv"
        self.mock = patch.object(plant, "DeyeClient", FakeClient)
        self.mock.start()
        self.output = io.StringIO()
        self.quiet = redirect_stdout(self.output)
        self.quiet.__enter__()

    def tearDown(self):
        self.quiet.__exit__(None, None, None)
        self.mock.stop()
        self.directory.cleanup()

    def get(self, **kwargs):
        return plant.get_plant_data(csv_path=self.path, env_file=self.root / ".env", **kwargs)

    def test_creates_csv_then_updates_without_duplicates_or_losing_values(self):
        FakeClient.samples = [sample("2026-04-01T08:00:00Z", 100), sample("2026-04-01T09:00:00Z", 200)]
        first = self.get(end_date="2026-04-01")
        self.assertEqual(len(first), 2)
        self.assertTrue(self.path.exists())
        self.assertEqual(pd.Timestamp(FakeClient.calls[0]["startTimestamp"], unit="s", tz="UTC"),
                         pd.Timestamp("2026-03-31T22:00:00Z"))
        FakeClient.samples = [sample("2026-04-01T08:00:00Z", 110, None), sample("2026-04-02T08:00:00Z", 300)]
        second = self.get(end_date="2026-04-02")
        self.assertEqual(len(second), 3)
        self.assertEqual(second.iloc[0]["generationPower"], 110)
        self.assertEqual(second.iloc[0]["consumptionPower"], 50)
        third = self.get(end_date="2026-04-02")
        self.assertEqual(len(third), 3)
        self.assertEqual(pd.Timestamp(FakeClient.calls[-1]["startTimestamp"], unit="s", tz="UTC"),
                         pd.Timestamp("2026-04-01T22:00:00Z"))
        saved = pd.read_csv(self.path)
        self.assertFalse(saved["timestamp_utc"].duplicated().any())
        self.assertTrue(self.path.with_suffix(".metadata.json").exists())

    def test_empty_api_still_creates_a_csv_with_headers(self):
        result = self.get(end_date="2026-04-01")
        self.assertTrue(result.empty)
        self.assertIn("timestamp_utc", pd.read_csv(self.path).columns)

    def test_failed_api_call_preserves_existing_csv(self):
        FakeClient.samples = [sample("2026-04-01T08:00:00Z", 100)]
        self.get(end_date="2026-04-01")
        before = self.path.read_bytes()
        with patch.object(FakeClient, "post", side_effect=RuntimeError("API unavailable")):
            with self.assertRaises(RuntimeError):
                self.get(end_date="2026-04-02")
        self.assertEqual(self.path.read_bytes(), before)

    def test_returns_only_requested_dates_even_if_api_includes_boundary(self):
        FakeClient.samples = [sample("2026-04-01T08:00:00Z", 100), sample("2026-04-01T22:00:00Z", 200)]
        result = self.get(end_date="2026-04-01")
        self.assertEqual(len(result), 1)

    def test_another_plant_cannot_be_merged_into_this_csv(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("station_id,timestamp_utc\n2,2026-04-01T08:00:00Z\n", encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "another plant"):
            self.get()
        self.assertEqual(self.path.read_bytes(), before)

    def test_truncated_api_response_is_not_saved_as_complete(self):
        with patch.object(FakeClient, "post", return_value={"total": 2, "stationDataItems": [sample("2026-04-01T08:00:00Z", 100)]}):
            with self.assertRaisesRegex(RuntimeError, "truncated"):
                self.get(end_date="2026-04-01")
        self.assertFalse(self.path.exists())

    def test_request_windows_stay_consecutive_across_clock_change(self):
        self.get(start_date="2025-10-01", end_date="2025-11-02")
        self.assertEqual(len(FakeClient.calls), 2)
        first, second = FakeClient.calls
        self.assertEqual(first["endTimestamp"] + 1, second["startTimestamp"])
        # This 30-local-day request includes the extra hour when clocks go back.
        self.assertEqual(first["endTimestamp"] + 1 - first["startTimestamp"], 721 * 3600)

    def test_seconds_milliseconds_and_date_strings_keep_the_same_instant(self):
        instant = pd.Timestamp("2026-04-01T08:00:00Z")
        for value in [instant.timestamp(), instant.timestamp() * 1000, instant.isoformat()]:
            with self.subTest(value_type=type(value).__name__):
                FakeClient.samples = [{"timeStamp": value, "generationPower": 100}]
                data = self.get(end_date="2026-04-01")
                self.assertEqual(len(data), 1)
                self.assertEqual(data.iloc[0]["timestamp_utc"], instant)

    def test_missing_timestamp_is_rejected_before_saving(self):
        FakeClient.samples = [{"timeStamp": None, "generationPower": 100}]
        with self.assertRaisesRegex(ValueError, "missing timestamp"):
            self.get(end_date="2026-04-01")
        self.assertFalse(self.path.exists())

    def test_first_download_reports_the_actual_csv_period(self):
        FakeClient.samples = [sample("2026-04-01T08:00:00Z", 100),
                              sample("2026-04-01T09:00:00Z", 200)]
        self.get(end_date="2026-04-01")
        report = json.loads(self.path.with_suffix(".metadata.json").read_text())
        self.assertIsNone(report["csv_period_before"])
        self.assertEqual(report["rows_before"], 0)
        self.assertEqual(report["rows_added"], 2)
        self.assertEqual(report["added_period"], report["csv_period_after"])
        self.assertEqual(report["csv_period_after"]["start_local"], "2026-04-01T10:00:00+02:00")
        self.assertIn("New rows added: 2 rows.", self.output.getvalue())

    def test_new_period_counts_backfilled_rows_but_not_corrections(self):
        FakeClient.samples = [sample("2026-04-01T08:00:00Z", 100),
                              sample("2026-04-02T08:00:00Z", 200)]
        self.get(end_date="2026-04-02")
        FakeClient.samples = [sample("2026-04-01T08:00:00Z", 110),
                              sample("2026-04-01T12:00:00Z", 150),
                              sample("2026-04-03T08:00:00Z", 300)]
        self.get(start_date="2026-04-01", end_date="2026-04-03")
        report = json.loads(self.path.with_suffix(".metadata.json").read_text())
        self.assertEqual(report["rows_before"], 2)
        self.assertEqual(report["rows"], 4)
        self.assertEqual(report["rows_added"], 2)
        self.assertEqual(report["csv_period_before"]["end_utc"], "2026-04-02T08:00:00+00:00")
        self.assertEqual(report["added_period"]["start_utc"], "2026-04-01T12:00:00+00:00")
        self.assertEqual(report["added_period"]["end_utc"], "2026-04-03T08:00:00+00:00")

    def test_unchanged_download_reports_zero_new_rows(self):
        FakeClient.samples = [sample("2026-04-01T08:00:00Z", 100)]
        self.get(end_date="2026-04-01")
        self.output.truncate(0)
        self.output.seek(0)
        self.get(end_date="2026-04-01")
        report = json.loads(self.path.with_suffix(".metadata.json").read_text())
        self.assertEqual(report["rows_added"], 0)
        self.assertIsNone(report["added_period"])
        self.assertEqual(report["csv_period_before"], report["csv_period_after"])
        self.assertIn("New rows added: 0 (no new timestamps found).", self.output.getvalue())


class PaginationTests(unittest.TestCase):
    def client(self, responses):
        client = object.__new__(plant.DeyeClient)
        client.post = unittest.mock.Mock(side_effect=responses)
        return client

    def test_station_list_reads_the_second_page(self):
        first = [{"id": i} for i in range(200)]
        client = self.client([{"stationList": first, "total": 201},
                              {"stationList": [{"id": 200}], "total": 201}])
        self.assertEqual(len(client.stations()), 201)
        self.assertEqual(client.post.call_count, 2)

    def test_repeated_page_does_not_create_a_false_complete_list(self):
        page = {"stationList": [{"id": i} for i in range(200)], "total": 401}
        client = self.client([page, page])
        with self.assertRaisesRegex(RuntimeError, "repeated"):
            client.stations()


if __name__ == "__main__":
    unittest.main()
