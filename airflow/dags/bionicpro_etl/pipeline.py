"""Логика ETL без зависимости от Airflow: её можно проверять отдельно."""

import hashlib
import json
import os
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID, uuid4


UTC = timezone.utc


def utc_datetime(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value is None or value.tzinfo is None:
        raise ValueError("Время должно содержать часовой пояс")
    return value.astimezone(UTC)


def choose_interval(report_date, interval_start, interval_end, now=None):
    """Ручная дата имеет приоритет; иначе используются границы расписания."""
    if report_date is not None:
        day = date.fromisoformat(report_date)
        if report_date != day.isoformat():
            raise ValueError("Дата должна иметь формат YYYY-MM-DD")
        start = datetime.combine(day, time.min, UTC)
        end = start + timedelta(days=1)
    else:
        start, end = utc_datetime(interval_start), utc_datetime(interval_end)
    if start.time() != time.min or end - start != timedelta(days=1):
        raise ValueError("Ожидается полный день UTC: [00:00, следующие 00:00)")
    if start.date() < date(1970, 1, 1):
        raise ValueError("Дата не поддерживается типом Date в витрине")
    if end > utc_datetime(now or datetime.now(UTC)):
        raise ValueError("Нельзя публиковать незавершённый день")
    return {"report_date": start.date().isoformat(),
            "start": start.isoformat(), "end": end.isoformat()}


def run_directory(run_id):
    # run_id не используется как имя пути напрямую.
    root = Path(os.environ.get("BIONICPRO_ETL_WORK_DIR", "/opt/airflow/work"))
    path = root / hashlib.sha256(run_id.encode()).hexdigest()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def save_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return str(path)


def read_json(path):
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream)


def source_connection(env_name):
    # Импорт и подключение только во время задачи, не при разборе DAG.
    import psycopg2

    connection = psycopg2.connect(
        os.environ[env_name], connect_timeout=10,
        options="-c statement_timeout=120000 -c timezone=UTC",
    )
    connection.set_session(isolation_level="REPEATABLE READ", readonly=True)
    return connection


def extract_crm(interval):
    from psycopg2.extras import RealDictCursor

    with closing(source_connection("BIONICPRO_CRM_DSN")) as connection:
        with connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("""SELECT client_id::text, owner_issuer, owner_subject
                              FROM crm.clients ORDER BY client_id""")
            clients = [dict(row) for row in cursor]
            cursor.execute("""SELECT prosthesis_id::text, client_id::text, model
                              FROM crm.prostheses ORDER BY prosthesis_id""")
            prostheses = [dict(row) for row in cursor]
    if not clients:
        raise ValueError("Источник CRM пуст. Сначала загрузите учебных клиентов")
    return save_json(Path(interval["work_dir"]) / "crm.json",
                     {"clients": clients, "prostheses": prostheses})


def extract_telemetry(interval):
    from psycopg2.extras import RealDictCursor

    path = Path(interval["work_dir"]) / "telemetry.jsonl"
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with closing(source_connection("BIONICPRO_TELEMETRY_DSN")) as connection:
            with connection, connection.cursor(
                name="report_events", cursor_factory=RealDictCursor,
            ) as cursor:
                cursor.itersize = 2000
                # Все доставки выбранных event_id, включая возможный конфликт
                # даты вне интервала. Иначе такой конфликт остался бы незамеченным.
                cursor.execute("""
                    WITH selected AS (
                        SELECT DISTINCT event_id FROM telemetry.movement_events
                        WHERE occurred_at >= %s AND occurred_at < %s
                    )
                    SELECT e.event_id::text, e.prosthesis_id::text,
                           e.occurred_at, e.response_time_ms
                    FROM telemetry.movement_events e
                    JOIN selected s USING (event_id)
                    ORDER BY e.event_id, e.ingestion_id
                """, (utc_datetime(interval["start"]), utc_datetime(interval["end"])))
                with temporary.open("x", encoding="utf-8") as stream:
                    os.chmod(temporary, 0o600)
                    for record in cursor:
                        row = dict(record)
                        row["occurred_at"] = utc_datetime(row["occurred_at"]).isoformat()
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return str(path)


def telemetry_rows(path):
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def aggregate(crm, events, interval):
    """События отсортированы по event_id: дубли не требуют хранения всех ID."""
    clients, owners, prostheses = {}, set(), {}
    for client in crm["clients"]:
        client_id = str(UUID(client["client_id"]))
        owner = (client["owner_issuer"], client["owner_subject"])
        if not all(isinstance(v, str) and v.strip() for v in owner):
            raise ValueError("В CRM отсутствует доверенная учётная запись")
        if client_id in clients or owner in owners:
            raise ValueError("Неоднозначная связь клиента и учётной записи")
        clients[client_id] = {**client, "client_id": client_id}
        owners.add(owner)
    for prosthesis in crm["prostheses"]:
        prosthesis_id = str(UUID(prosthesis["prosthesis_id"]))
        client_id = str(UUID(prosthesis["client_id"]))
        if prosthesis_id in prostheses or client_id not in clients:
            raise ValueError("Неоднозначная или отсутствующая принадлежность протеза")
        if not isinstance(prosthesis["model"], str) or not prosthesis["model"].strip():
            raise ValueError("В CRM отсутствует модель протеза")
        prostheses[prosthesis_id] = {**prosthesis, "client_id": client_id}

    start, end = utc_datetime(interval["start"]), utc_datetime(interval["end"])
    previous_id, previous_data, groups = None, None, {}
    for event in events:
        event_id = str(UUID(event["event_id"]))
        prosthesis_id = str(UUID(event["prosthesis_id"]))
        occurred_at = utc_datetime(event["occurred_at"])
        response_ms = event["response_time_ms"]
        if type(response_ms) is not int or response_ms < 0:
            raise ValueError("Время реакции должно быть неотрицательным целым числом")
        values = (prosthesis_id, occurred_at, response_ms)
        if event_id == previous_id:
            if values != previous_data:
                raise ValueError("Конфликт данных у одного event_id")
            continue
        if previous_id is not None and event_id < previous_id:
            raise ValueError("Нарушена сортировка событий по event_id")
        if not start <= occurred_at < end:
            raise ValueError("Событие вне интервала: проверьте конфликт event_id")
        if prosthesis_id not in prostheses:
            raise ValueError("Событие относится к неизвестному протезу")
        previous_id, previous_data = event_id, values
        count, total = groups.get(prosthesis_id, (0, 0))
        groups[prosthesis_id] = (count + 1, total + response_ms)

    rows = []
    for prosthesis_id, (count, total) in sorted(groups.items()):
        prosthesis = prostheses[prosthesis_id]
        client = clients[prosthesis["client_id"]]
        rows.append({
            "owner_issuer": client["owner_issuer"], "owner_subject": client["owner_subject"],
            "client_id": client["client_id"], "prosthesis_id": prosthesis_id,
            "model": prosthesis["model"], "report_date": interval["report_date"],
            "movement_count": count, "avg_response_time_ms": total / count,
        })
    return rows


def build_mart(interval, crm_path, telemetry_path):
    rows = aggregate(read_json(crm_path), telemetry_rows(telemetry_path), interval)
    return save_json(Path(interval["work_dir"]) / "mart.json", rows)


class ClickHouse:
    def __init__(self):
        self.url = os.environ["BIONICPRO_CLICKHOUSE_URL"].rstrip("/")
        self.user = os.environ["BIONICPRO_CLICKHOUSE_USER"]
        self.password = os.environ["BIONICPRO_CLICKHOUSE_PASSWORD"]

    def request(self, query, parameters=None, body=b""):
        # Секреты передаются заголовками, не в URL и не в журнале задачи.
        args = {"query": query, "wait_end_of_query": "1"}
        args.update({"param_" + k: str(v) for k, v in (parameters or {}).items()})
        request = Request(self.url + "/?" + urlencode(args), data=body, method="POST",
                          headers={"X-ClickHouse-User": self.user,
                                   "X-ClickHouse-Key": self.password,
                                   "Content-Type": "application/octet-stream"})
        try:
            with urlopen(request, timeout=120) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            # Ответ сервера может содержать значения данных; в лог не переносим.
            raise RuntimeError(f"ClickHouse вернул HTTP {exc.code}; операция не подтверждена") from None

    def select(self, query, parameters=None):
        raw = self.request(query + " FORMAT JSONEachRow", parameters)
        return [json.loads(line) for line in raw.splitlines() if line.strip()]

    def insert(self, table, rows):
        if table not in {"daily_prosthesis_usage", "report_publications"}:
            raise ValueError("Недопустимая таблица назначения")
        if rows:
            body = "\n".join(json.dumps(row, ensure_ascii=False, allow_nan=False) for row in rows)
            self.request(f"INSERT INTO reports.{table} FORMAT JSONEachRow", body=body.encode())


def verify_version(client, manifest):
    rows = client.select("""
        SELECT count() AS row_count,
               uniqExact(tuple(owner_issuer, owner_subject, prosthesis_id, report_date)) AS unique_rows,
               sum(movement_count) AS event_count,
               countIf(report_date != {report_date:Date}) AS wrong_day
        FROM reports.daily_prosthesis_usage
        WHERE publication_id = {publication_id:UUID}
    """, {"report_date": manifest["report_date"], "publication_id": manifest["publication_id"]})
    if len(rows) != 1:
        raise ValueError("ClickHouse не подтвердил проверку версии")
    actual = {key: int(value) for key, value in rows[0].items()}
    expected = {"row_count": manifest["row_count"], "unique_rows": manifest["row_count"],
                "event_count": manifest["event_count"], "wrong_day": 0}
    if actual != expected:
        raise ValueError("Версия витрины загружена неполностью или содержит дубли")


def load_version(interval, mart_path, client=None):
    client = client or ClickHouse()
    rows = read_json(mart_path)
    # Новая попытка — новый ID. Частичные данные прошлой попытки не публикуются.
    publication_id = str(uuid4())
    manifest = {"report_date": interval["report_date"], "publication_id": publication_id,
                "row_count": len(rows), "event_count": sum(r["movement_count"] for r in rows)}
    for offset in range(0, len(rows), 1000):
        client.insert("daily_prosthesis_usage",
                      [{**r, "publication_id": publication_id} for r in rows[offset:offset + 1000]])
    verify_version(client, manifest)
    return manifest


def publish_period(manifest, run_id, client=None):
    client = client or ClickHouse()
    verify_version(client, manifest)
    existing = client.select("""
        SELECT report_date, row_count, event_count FROM reports.report_publications
        WHERE publication_id = {publication_id:UUID}
    """, {"publication_id": manifest["publication_id"]})
    if existing:
        for row in existing:
            if (row["report_date"], int(row["row_count"]), int(row["event_count"])) != (
                manifest["report_date"], manifest["row_count"], manifest["event_count"],
            ):
                raise ValueError("Конфликт в реестре публикаций")
        return manifest["publication_id"]
    # Время публикации задаёт сам ClickHouse. Один INSERT — одна запись готовности.
    client.request("""
        INSERT INTO reports.report_publications
            (report_date, publication_id, published_at, row_count, event_count, airflow_run_id)
        SELECT {report_date:Date}, {publication_id:UUID}, now64(6),
               {row_count:UInt64}, {event_count:UInt64}, {airflow_run_id:String}
    """, {**manifest, "airflow_run_id": run_id})
    return manifest["publication_id"]
