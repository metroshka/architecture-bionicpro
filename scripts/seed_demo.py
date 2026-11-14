#!/usr/bin/env python3
"""Загрузить синтетическую CRM и телеметрию, не изменяя пользователей Keycloak."""

import argparse
import csv
import io
import json
import subprocess
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid5


ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = UUID("0ac77251-bf19-48d6-a588-35d68b839826")
ISSUER = "http://localhost:8080/realms/reports-realm"


def stable_id(name):
    return str(uuid5(NAMESPACE, name))


def compose(*args, input_text=None):
    result = subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, input=input_text,
        text=True, capture_output=True, timeout=120,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def keycloak_subject(username):
    # Пароль остаётся в окружении контейнера; токены CLI не сохраняются.
    script = '''set -euo pipefail
: "${KEYCLOAK_ADMIN:?}"
: "${KEYCLOAK_ADMIN_PASSWORD:?}"
/opt/keycloak/bin/kcadm.sh get users -r reports-realm \\
  -q "username=$1" -q exact=true --fields id,username --no-config \\
  --server http://localhost:8080 --realm master \\
  --user "$KEYCLOAK_ADMIN" --password "$KEYCLOAK_ADMIN_PASSWORD"
'''
    users = json.loads(compose("exec", "-T", "keycloak", "bash", "-s", "--", username,
                               input_text=script))
    matches = [u for u in users if u.get("username") == username and u.get("id")]
    if len(matches) != 1:
        raise ValueError("В Keycloak ожидается ровно один пользователь " + username)
    return matches[0]["id"]


def fixtures(day, subjects):
    clients = [
        (stable_id("client:" + login), ISSUER, subjects[login])
        for login in ("user1", "user2")
    ]
    prostheses = [
        (stable_id("prosthesis:1"), clients[0][0], "DEMO-Hand-A"),
        (stable_id("prosthesis:2"), clients[0][0], "DEMO-Hand-B"),
        (stable_id("prosthesis:3"), clients[1][0], "DEMO-Hand-A"),
    ]
    events = []

    def add_events(event_day, number, response_times):
        for index, response_ms in enumerate(response_times):
            key = f"{event_day}:prosthesis:{number}:event:{index}"
            occurred_at = datetime.combine(event_day, time(10, index), timezone.utc)
            events.append((
                stable_id("delivery:" + key), stable_id("event:" + key),
                stable_id(f"prosthesis:{number}"), occurred_at.isoformat(), response_ms,
            ))

    # Последний день: 8 движений и одна повторная доставка того же события.
    add_events(day, 1, [80, 100, 120])
    add_events(day, 2, [60, 80])
    add_events(day, 3, [90, 100, 110])
    events.append((stable_id(f"duplicate-delivery:{day}"), *events[0][1:]))
    # Средний день оставлен без событий. Более ранний день нужен для диапазона.
    add_events(day - timedelta(days=2), 1, [70, 90])
    add_events(day - timedelta(days=2), 3, [105])
    return clients, prostheses, events


def fixture_sql(table, columns, key, rows):
    """COPY безопасно переносит значения; совпадение ID не перезаписывает данные."""
    temp = "seed_" + table.split(".")[1]
    fields = ", ".join(columns)
    old = ", ".join("t." + c for c in columns)
    new = ", ".join("s." + c for c in columns)
    payload = io.StringIO()
    csv.writer(payload, lineterminator="\n").writerows(rows)
    return f"""
CREATE TEMP TABLE {temp} (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP;
COPY {temp} ({fields}) FROM STDIN WITH (FORMAT csv);
{payload.getvalue()}\\.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM {temp} s JOIN {table} t USING ({key})
        WHERE ({old}) IS DISTINCT FROM ({new})
    ) THEN
        RAISE EXCEPTION 'Учебные ID уже заняты другими данными в {table}; загрузка остановлена';
    END IF;
END; $$;
INSERT INTO {table} ({fields}) SELECT {fields} FROM {temp}
ON CONFLICT ({key}) DO NOTHING;
"""


def psql(service, user, database, sql):
    return compose("exec", "-T", service, "psql", "-X", "-v", "ON_ERROR_STOP=1",
                   "-U", user, "-d", database, input_text=sql)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True, type=date.fromisoformat,
                        help="последний учебный день UTC, YYYY-MM-DD; должен быть завершён")
    args = parser.parse_args()
    if args.day >= datetime.now(timezone.utc).date():
        parser.error("Укажите завершённый день UTC, не сегодня и не будущую дату.")

    subjects = {login: keycloak_subject(login) for login in ("user1", "user2")}
    if len(set(subjects.values())) != 2:
        raise ValueError("Учётные записи должны иметь разные идентификаторы")
    clients, prostheses, events = fixtures(args.day, subjects)

    crm_sql = "BEGIN;\n" + fixture_sql(
        "crm.clients", ["client_id", "owner_issuer", "owner_subject"], "client_id", clients,
    ) + fixture_sql(
        "crm.prostheses", ["prosthesis_id", "client_id", "model"], "prosthesis_id", prostheses,
    ) + "COMMIT;\n"
    psql("crm_db", "crm_owner", "crm", crm_sql)

    telemetry_sql = "BEGIN;\n" + fixture_sql(
        "telemetry.movement_events",
        ["ingestion_id", "event_id", "prosthesis_id", "occurred_at", "response_time_ms"],
        "ingestion_id", events,
    ) + "COMMIT;\n"
    psql("telemetry_db", "telemetry_owner", "telemetry", telemetry_sql)

    print("CRM: учебные клиенты связаны с действующими user1 и user2 из Keycloak.")
    print(psql("crm_db", "crm_owner", "crm", """
SELECT c.client_id, p.model, p.prosthesis_id
FROM crm.clients c JOIN crm.prostheses p USING (client_id)
ORDER BY c.client_id, p.prosthesis_id;
"""))
    print("Телеметрия: фактические количества за учебный диапазон:")
    # Даты получены из date.fromisoformat, произвольный SQL сюда не попадает.
    print(psql("telemetry_db", "telemetry_owner", "telemetry", f"""
SELECT (occurred_at AT TIME ZONE 'UTC')::date AS day_utc,
       count(*) AS received_messages, count(DISTINCT event_id) AS movements
FROM telemetry.movement_events
WHERE occurred_at >= '{args.day - timedelta(days=2)}T00:00:00+00:00'::timestamptz
  AND occurred_at < '{args.day + timedelta(days=1)}T00:00:00+00:00'::timestamptz
GROUP BY day_utc ORDER BY day_utc;
"""))
    print(f"День без событий: {args.day - timedelta(days=1)} (при первой загрузке).")
    print("Скрипт загружает только источники. Витрину ClickHouse и реестр готовности заполняет DAG.")
    print("Повтор команды с той же датой не добавляет дубли и не перезаписывает данные.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        raise SystemExit("Загрузка не завершена: " + str(exc)) from exc
