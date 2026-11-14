import unittest
from datetime import date
from unittest.mock import Mock
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth import Owner
from app.config import Settings
from app.errors import ApiError, warehouse_unavailable
from app.main import create_app
from app.reports import ReportPeriod, ReportService


SETTINGS = Settings("http://clickhouse:8123", "reports_reader", "test",
                    "http://keycloak/realms/reports", "http://keycloak/certs")
OWNER = Owner(SETTINGS.issuer, "owner-one")
VERSION = "11111111-1111-4111-8111-111111111111"
NEW_VERSION = "22222222-2222-4222-8222-222222222222"
ITEM = {"report_date": "2026-11-10",
        "prosthesis_id": "33333333-3333-4333-8333-333333333333",
        "model": "DEMO-Hand-A", "movement_count": "3", "avg_response_time_ms": 100.0}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(SETTINGS)
        self.warehouse = Mock()
        self.app.state.warehouse = self.warehouse
        self.app.state.verifier = Mock()
        self.app.state.verifier.verify.return_value = OWNER
        self.client = TestClient(self.app)

    def get_report(self, **params):
        return self.client.get("/reports", params=params)

    def test_ready_report_contains_precomputed_values(self):
        self.warehouse.select.side_effect = [
            [{"report_date": "2026-11-10", "publication_id": VERSION}], [ITEM]]
        result = self.get_report(date_from="2026-11-10", date_to="2026-11-10")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "ready")
        self.assertEqual(result.json()["row_count"], 1)
        self.assertEqual(result.json()["items"][0]["movement_count"], 3)
        self.assertEqual(result.json()["items"][0]["avg_response_time_ms"], 100)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertIn(".json", result.headers["content-disposition"])

    def test_published_day_without_own_rows_is_successfully_empty(self):
        self.warehouse.select.side_effect = [
            [{"report_date": "2026-11-09", "publication_id": VERSION}], []]
        result = self.get_report(date_from="2026-11-09", date_to="2026-11-09")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "empty")
        self.assertEqual(result.json()["items"], [])

    def test_missing_day_inside_range_is_not_a_partial_report(self):
        self.warehouse.select.return_value = [
            {"report_date": day, "publication_id": VERSION}
            for day in ("2026-11-08", "2026-11-10")]
        result = self.get_report(date_from="2026-11-08", date_to="2026-11-10")
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["missing_dates"], ["2026-11-09"])
        self.assertEqual(self.warehouse.select.call_count, 1)

    def test_unpublished_day_is_not_a_successful_empty_report(self):
        self.warehouse.select.return_value = []
        result = self.get_report(date_from="2026-11-07", date_to="2026-11-07")
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["error"], "period_not_ready")

    def test_unfinished_day_remains_unavailable_even_with_bad_registry_entry(self):
        self.warehouse.select.return_value = [
            {"report_date": "2026-11-10", "publication_id": VERSION}]
        with self.assertRaises(ApiError) as raised:
            ReportService(self.warehouse).get_report(
                OWNER, ReportPeriod(date_from="2026-11-10", date_to="2026-11-10"),
                today=date(2026, 11, 10))
        self.assertEqual(raised.exception.status, 409)

    def test_invalid_ranges_are_rejected_before_reading_clickhouse(self):
        for start, end in [("2026-11-10", "2026-11-08"), ("2026-01-01", "2026-02-01"),
                           ("2026-02-30", "2026-03-01"), ("20261110", "2026-11-10")]:
            with self.subTest(start=start, end=end):
                result = self.get_report(date_from=start, date_to=end)
                self.assertEqual(result.status_code, 422)
        self.warehouse.select.assert_not_called()

    def test_31_days_is_allowed(self):
        period = ReportPeriod(date_from="2026-01-01", date_to="2026-01-31")
        self.assertEqual(len(period.days()), 31)

    def test_extra_owner_parameter_is_not_accepted(self):
        result = self.get_report(date_from="2026-11-10", date_to="2026-11-10", user_id="someone-else")
        self.assertEqual(result.status_code, 422)
        self.warehouse.select.assert_not_called()

    def test_storage_failure_does_not_become_empty_report(self):
        self.warehouse.select.side_effect = warehouse_unavailable()
        result = self.get_report(date_from="2026-11-10", date_to="2026-11-10")
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["error"], "report_storage_unavailable")

    def test_report_is_not_silently_truncated(self):
        self.warehouse.select.side_effect = [
            [{"report_date": "2026-11-10", "publication_id": VERSION}], [ITEM] * 10001]
        result = self.get_report(date_from="2026-11-10", date_to="2026-11-10")
        self.assertEqual(result.status_code, 413)

    def test_publication_between_reads_does_not_change_selected_version(self):
        current = {"version": VERSION}

        def select(query, params):
            if "reports.ready_periods" in query:
                snapshot = [{"report_date": "2026-11-10", "publication_id": current["version"]}]
                current["version"] = NEW_VERSION
                return snapshot
            # Имитируем публикацию новых итогов после получения реестра.
            selected_count = 3 if params["version_0"] == UUID(VERSION) else 6
            return [{**ITEM, "movement_count": selected_count}]

        self.warehouse.select.side_effect = select
        result = self.get_report(date_from="2026-11-10", date_to="2026-11-10")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["items"][0]["movement_count"], 3)
        self.assertEqual(current["version"], NEW_VERSION)


if __name__ == "__main__":
    unittest.main()
