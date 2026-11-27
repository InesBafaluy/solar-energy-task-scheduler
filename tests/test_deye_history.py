import csv
from datetime import date
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import io
from zoneinfo import ZoneInfo

MODULE = Path(__file__).resolve().parents[1] / "src/collect_data/get_deye_history.py"
spec = importlib.util.spec_from_file_location("deye_history", MODULE)
deye = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deye)


class FakeClient:
    def __init__(self):
        self.calls = []

    def post(self, endpoint, body):
        self.calls.append((endpoint, body))
        if endpoint == "/station/device":
            return {"success": True, "total": 1, "deviceListItems": [
                {"deviceSn": "SYNTHETIC", "deviceType": "INVERTER"}]}
        if endpoint == "/device/measurePoints":
            return {"success": True, "measurePoints": ["SOC", "PVPower"]}
        if endpoint == "/device/historyRaw":
            return {"success": True, "dataList": [{"time": str(body["startTimestamp"]), "itemList": [
                {"key": "SOC", "value": "87", "unit": "%"},
                {"key": "PVPower", "value": "1400", "unit": "W"}]}]}
        if endpoint == "/station/history/power":
            return {"success": True, "total": 1, "stationDataItems": [
                {"timeStamp": str(body["startTimestamp"]), "generationPower": 1400,
                 "consumptionPower": None}]}
        if endpoint == "/station/history":
            return {"success": True, "total": 1, "stationDataItems": [
                {"year": 2025, "month": 10, "day": 26, "generationValue": 4.2}]}
        raise AssertionError(endpoint)


class ExportTests(unittest.TestCase):
    def test_dst_day_and_inclusive_end_date(self):
        zone = ZoneInfo("Europe/Andorra")
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            archive = deye.Archive(client, directory)
            day = date(2025, 10, 26)
            result = deye.export_station(archive, {"id": 1}, day, day, zone)
            body = next(b for endpoint, b in client.calls if endpoint == "/device/historyRaw")
            self.assertEqual(body["endTimestamp"] - body["startTimestamp"] + 1, 25 * 3600)
            self.assertEqual(body["measurePoints"], ["SOC", "PVPower"])
            self.assertEqual(result["device_measurement_rows"], 2)
            prefix = Path(directory) / "csv/1/2025-10-26_2025-10-26"
            with (prefix / "station_power.csv").open(encoding="utf-8-sig", newline="") as file:
                row = next(csv.DictReader(file))
            self.assertEqual(row["consumptionPower"], "")
            self.assertEqual(row["timestamp_utc"], "2025-10-25T22:00:00+00:00")
            with (prefix / "device_measurements.csv").open(encoding="utf-8-sig", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual({r["unit"] for r in rows}, {"W", "%"})
            self.assertEqual(result["failures"], [])
            count = len(client.calls)
            deye.export_station(archive, {"id": 1}, day, day, zone)
            # Only device discovery is deliberately refreshed on a resumed run.
            self.assertEqual(len(client.calls), count + 1)

    def test_ambiguous_and_nonexistent_local_times_are_not_guessed(self):
        zone = ZoneInfo("Europe/Andorra")
        self.assertEqual(deye.parse_time("2025-10-26 02:30:00", zone), ("", "ambiguous_local_time"))
        self.assertEqual(deye.parse_time("2025-03-30 02:30:00", zone), ("", "nonexistent_local_time"))
        self.assertEqual(deye.parse_time("2025-10-26T02:30:00+02:00", zone)[0], "2025-10-26T00:30:00+00:00")

    def test_daily_windows_have_no_gaps_or_overlap(self):
        windows = list(deye.dates(date(2025, 1, 1), date(2025, 2, 1), 31))
        self.assertEqual(windows, [(date(2025, 1, 1), date(2025, 2, 1)),
                                   (date(2025, 2, 1), date(2025, 2, 2))])

    def test_server_truncation_is_reported(self):
        client = FakeClient()
        original = client.post

        def truncated(endpoint, body):
            result = original(endpoint, body)
            if endpoint == "/station/history/power":
                result["total"] = 9999
            return result

        client.post = truncated
        with tempfile.TemporaryDirectory() as directory:
            report = deye.export_station(deye.Archive(client, directory), {"id": 1},
                                         date(2025, 1, 1), date(2025, 1, 1), ZoneInfo("UTC"))
            self.assertEqual(report["station_power_rows"], 0)
            self.assertEqual(len(report["failures"]), 1)

    def test_empty_history_is_reported_and_csv_has_headers(self):
        client = FakeClient()
        original = client.post

        def empty(endpoint, body):
            result = original(endpoint, body)
            if "history" in endpoint:
                key = "dataList" if endpoint == "/device/historyRaw" else "stationDataItems"
                result[key] = []
                result["total"] = 0
            return result

        client.post = empty
        with tempfile.TemporaryDirectory() as directory:
            report = deye.export_station(deye.Archive(client, directory), {"id": 1},
                                         date(2025, 1, 1), date(2025, 1, 1), ZoneInfo("UTC"))
            self.assertEqual(len(report["empty_requests"]), 3)
            self.assertEqual(report["device_measurement_rows"], 0)
            header = next(Path(directory).rglob("device_measurements.csv")).read_text(encoding="utf-8-sig")
            self.assertIn("timestamp_utc", header)

    def test_pagination_does_not_silently_drop_full_pages(self):
        class Pages:
            def get(self, endpoint, body, cache):
                start = (body["page"] - 1) * 200
                return {"total": 201, "stationList": [{"id": i} for i in range(start, min(start + 200, 201))]}
        self.assertEqual(len(deye.paginated(Pages(), "/station/list", {}, "stationList")), 201)

    def test_login_hashes_password_without_saving_token(self):
        client = deye.Client()
        captured = []
        client.post = lambda endpoint, body, auth: captured.append((endpoint, body.copy(), auth)) or {
            "accessToken": "synthetic-token"}
        with patch.dict("os.environ", {}, clear=True), patch("builtins.input", side_effect=["123", "email", "test@example.com"]), \
                patch("getpass.getpass", side_effect=["synthetic-secret", "synthetic-password"]):
            client.login()
        body = captured[0][1]
        self.assertEqual(body["password"], hashlib.sha256(b"synthetic-password").hexdigest())
        self.assertEqual(client.token, "synthetic-token")
        self.assertFalse(captured[0][2])

    def test_rate_limit_retries_and_honors_retry_after(self):
        client = deye.Client(interval=0)
        response = io.StringIO(json.dumps({"success": True, "stationList": []}))
        class Opener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise HTTPError(request.full_url, 429, "rate limit", {"Retry-After": "9"}, None)
                return response
        client.opener = Opener()
        with patch.object(deye.time, "sleep") as sleep:
            result = client.post("/station/list")
        self.assertTrue(result["success"])
        self.assertEqual(client.opener.calls, 2)
        sleep.assert_any_call(9)

    def test_http_auth_failure_stops_instead_of_becoming_an_empty_day(self):
        client = FakeClient()
        client.post = lambda endpoint, body: (_ for _ in ()).throw(deye.AuthenticationError("HTTP 403"))
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(deye.AuthenticationError):
            deye.export_station(deye.Archive(client, directory), {"id": 1},
                                date(2025, 1, 1), date(2025, 2, 1), ZoneInfo("UTC"))


if __name__ == "__main__":
    unittest.main()
