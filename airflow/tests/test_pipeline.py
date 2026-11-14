"""Проверки результатов ETL и границы публикации, без запуска Airflow и БД."""

import copy
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dags"))
from bionicpro_etl import pipeline as p


def uid(number):
    return str(UUID(int=number))


NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
INTERVAL = p.choose_interval("2026-11-10", None, None, now=NOW)
CRM = {
    "clients": [
        {"client_id": uid(1), "owner_issuer": "https://idp.test", "owner_subject": "user-1"},
        {"client_id": uid(2), "owner_issuer": "https://idp.test", "owner_subject": "user-2"},
    ],
    "prostheses": [
        {"prosthesis_id": uid(11), "client_id": uid(1), "model": "DEMO-A"},
        {"prosthesis_id": uid(12), "client_id": uid(1), "model": "DEMO-B"},
        {"prosthesis_id": uid(13), "client_id": uid(2), "model": "DEMO-A"},
    ],
}


def event(number, prosthesis, response, at="2026-11-10T10:00:00+00:00"):
    return {"event_id": uid(number), "prosthesis_id": uid(prosthesis),
            "response_time_ms": response, "occurred_at": at}


EVENTS = [event(101, 11, 80), event(101, 11, 80), event(102, 11, 120),
          event(103, 12, 70), event(104, 13, 110)]


class MemoryWarehouse:
    """Имитирует только подтверждение записи, а не синтаксис SQL ClickHouse."""
    def __init__(self):
        self.rows = []
        self.publications = []
        self.fail_after_insert = False

    def insert(self, table, rows):
        assert table == "daily_prosthesis_usage"
        self.rows.extend(copy.deepcopy(rows))
        if self.fail_after_insert:
            self.fail_after_insert = False
            raise TimeoutError("Ответ после записи потерян")

    def select(self, query, parameters):
        version = parameters["publication_id"]
        if "FROM reports.report_publications" in query:
            return [{key: row[key] for key in ("report_date", "row_count", "event_count")}
                    for row in self.publications if row["publication_id"] == version]
        selected = [row for row in self.rows if row["publication_id"] == version]
        return [{
            "row_count": len(selected),
            "unique_rows": len({(r["owner_issuer"], r["owner_subject"], r["prosthesis_id"],
                                  r["report_date"]) for r in selected}),
            "event_count": sum(r["movement_count"] for r in selected),
            "wrong_day": sum(r["report_date"] != parameters["report_date"] for r in selected),
        }]

    def request(self, query, parameters):
        assert "INSERT INTO reports.report_publications" in query
        self.publications.append(dict(parameters))


class IntervalTests(unittest.TestCase):
    def test_manual_and_scheduled_intervals_match(self):
        self.assertEqual(INTERVAL, p.choose_interval(
            None, "2026-11-10T03:00:00+03:00", "2026-11-11T03:00:00+03:00", now=NOW))

    def test_today_and_future_are_rejected(self):
        for day in ("2026-11-11", "2026-11-12"):
            with self.subTest(day=day), self.assertRaises(ValueError):
                p.choose_interval(day, None, None, now=NOW)

    def test_non_daily_and_naive_intervals_are_rejected(self):
        cases = [("2026-11-10T00:00:00", "2026-11-11T00:00:00"),
                 ("2026-11-10T00:00:00Z", "2026-11-10T12:00:00Z"),
                 ("2026-11-09T01:00:00Z", "2026-11-10T01:00:00Z")]
        for start, end in cases:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                p.choose_interval(None, start, end, now=NOW)


class AggregationTests(unittest.TestCase):
    def test_ownership_multiple_prostheses_metrics_and_duplicate(self):
        rows = p.aggregate(CRM, EVENTS, INTERVAL)
        actual = {(r["owner_subject"], r["prosthesis_id"]):
                  (r["movement_count"], r["avg_response_time_ms"], r["model"]) for r in rows}
        self.assertEqual(actual, {("user-1", uid(11)): (2, 100, "DEMO-A"),
                                  ("user-1", uid(12)): (1, 70, "DEMO-B"),
                                  ("user-2", uid(13)): (1, 110, "DEMO-A")})

    def test_empty_day_has_no_fabricated_activity(self):
        self.assertEqual(p.aggregate(CRM, [], INTERVAL), [])

    def test_conflicting_duplicate_blocks_output(self):
        with self.assertRaisesRegex(ValueError, "Конфликт"):
            p.aggregate(CRM, [event(101, 11, 80), event(101, 11, 90)], INTERVAL)

    def test_conflicting_duplicate_on_another_day_blocks_output(self):
        with self.assertRaises(ValueError):
            p.aggregate(CRM, [event(101, 11, 80),
                             event(101, 11, 80, "2026-11-09T10:00:00Z")], INTERVAL)

    def test_unknown_prosthesis_blocks_output(self):
        with self.assertRaisesRegex(ValueError, "неизвестному"):
            p.aggregate(CRM, [event(101, 99, 80)], INTERVAL)

    def test_ambiguous_owner_blocks_even_an_empty_day(self):
        crm = copy.deepcopy(CRM)
        crm["clients"][1]["owner_subject"] = "user-1"
        with self.assertRaisesRegex(ValueError, "Неоднозначная"):
            p.aggregate(crm, [], INTERVAL)

    def test_half_open_interval(self):
        self.assertEqual(p.aggregate(CRM, [event(101, 11, 80, INTERVAL["start"])],
                                     INTERVAL)[0]["movement_count"], 1)
        with self.assertRaises(ValueError):
            p.aggregate(CRM, [event(101, 11, 80, INTERVAL["end"])], INTERVAL)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "mart.json")
        self.client = MemoryWarehouse()
        p.save_json(self.path, p.aggregate(CRM, EVENTS, INTERVAL))

    def test_loading_does_not_mark_day_ready(self):
        manifest = p.load_version(INTERVAL, self.path, self.client)
        self.assertEqual(self.client.publications, [])
        p.publish_period(manifest, "test-run", self.client)
        self.assertEqual(len(self.client.publications), 1)
        self.assertEqual(self.client.publications[0]["event_count"], 4)

    def test_lost_insert_response_does_not_publish_partial_version(self):
        self.client.fail_after_insert = True
        with self.assertRaises(TimeoutError):
            p.load_version(INTERVAL, self.path, self.client)
        abandoned_id = self.client.rows[0]["publication_id"]
        self.assertEqual(self.client.publications, [])
        manifest = p.load_version(INTERVAL, self.path, self.client)
        self.assertNotEqual(manifest["publication_id"], abandoned_id)
        p.publish_period(manifest, "retry", self.client)
        self.assertEqual(self.client.publications[0]["event_count"], 4)

    def test_incomplete_load_is_not_published(self):
        manifest = p.load_version(INTERVAL, self.path, self.client)
        self.client.rows.pop()
        with self.assertRaises(ValueError):
            p.publish_period(manifest, "incomplete", self.client)
        self.assertEqual(self.client.publications, [])

    def test_retry_of_publishing_is_idempotent(self):
        manifest = p.load_version(INTERVAL, self.path, self.client)
        p.publish_period(manifest, "test-run", self.client)
        p.publish_period(manifest, "test-run", self.client)
        self.assertEqual(len(self.client.publications), 1)

    def test_reprocessing_does_not_sum_old_and_new_versions(self):
        first = p.load_version(INTERVAL, self.path, self.client)
        p.publish_period(first, "first", self.client)
        second = p.load_version(INTERVAL, self.path, self.client)
        p.publish_period(second, "second", self.client)
        self.assertNotEqual(first["publication_id"], second["publication_id"])
        selected = [r for r in self.client.rows
                    if r["publication_id"] == self.client.publications[-1]["publication_id"]]
        self.assertEqual(sum(r["movement_count"] for r in selected), 4)

    def test_empty_day_is_published_separately_from_rows(self):
        p.save_json(self.path, [])
        manifest = p.load_version(INTERVAL, self.path, self.client)
        p.publish_period(manifest, "empty", self.client)
        self.assertEqual(self.client.rows, [])
        self.assertEqual(self.client.publications[0]["row_count"], 0)
        self.assertEqual(self.client.publications[0]["event_count"], 0)

    def test_late_event_is_included_in_new_version(self):
        first = p.load_version(INTERVAL, self.path, self.client)
        p.publish_period(first, "before-late-event", self.client)
        p.save_json(self.path, p.aggregate(CRM, EVENTS + [event(105, 11, 100)], INTERVAL))
        second = p.load_version(INTERVAL, self.path, self.client)
        p.publish_period(second, "after-late-event", self.client)
        self.assertEqual(self.client.publications[-1]["event_count"], 5)
        self.assertEqual(self.client.publications[0]["event_count"], 4)


if __name__ == "__main__":
    unittest.main()
