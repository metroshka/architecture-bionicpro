"""Дневная витрина: CRM + телеметрия → ClickHouse → публикация готовности."""

from datetime import timedelta

import pendulum
from airflow.sdk import DAG, Param, get_current_context, task
from airflow.timetables.interval import CronDataIntervalTimetable

from bionicpro_etl import pipeline


with DAG(
    dag_id="bionicpro_daily_reports",
    description="Подготовка дневных отчётов о работе собственных протезов",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    schedule=CronDataIntervalTimetable("0 0 * * *", timezone="UTC"),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=2,
    is_paused_upon_creation=True,
    default_args={"owner": "bionicpro", "retries": 2,
                  "retry_delay": timedelta(seconds=30),
                  "execution_timeout": timedelta(minutes=10)},
    params={"report_date": Param(None, type=["null", "string"], format="date",
                                 title="День UTC для ручной обработки",
                                 description="YYYY-MM-DD. Пустое значение — интервал расписания.")},
    tags=["bionicpro", "reports", "etl"],
    doc_md="""
    По расписанию обрабатывается предыдущий завершённый день UTC.
    Для исторического дня или поздней телеметрии задайте `report_date` вручную.
    Последняя задача публикует день, только если все предыдущие задачи успешны.
    В XCom передаются пути и метаданные, исходные данные остаются в локальном томе.
    """,
) as dag:

    @task(show_return_value_in_logs=False)
    def select_interval():
        context = get_current_context()
        interval = pipeline.choose_interval(
            context["params"].get("report_date"),
            context.get("data_interval_start"), context.get("data_interval_end"),
        )
        interval["work_dir"] = str(pipeline.run_directory(context["run_id"]))
        return interval

    @task(show_return_value_in_logs=False)
    def extract_crm(interval):
        return pipeline.extract_crm(interval)

    @task(show_return_value_in_logs=False)
    def extract_telemetry(interval):
        return pipeline.extract_telemetry(interval)

    @task(show_return_value_in_logs=False)
    def build_mart(interval, crm_path, telemetry_path):
        return pipeline.build_mart(interval, crm_path, telemetry_path)

    @task(show_return_value_in_logs=False)
    def load_version(interval, mart_path):
        return pipeline.load_version(interval, mart_path)

    @task(show_return_value_in_logs=False)
    def publish_period(manifest):
        return pipeline.publish_period(manifest, get_current_context()["run_id"])

    interval = select_interval()
    crm_path = extract_crm(interval)
    telemetry_path = extract_telemetry(interval)
    mart_path = build_mart(interval, crm_path, telemetry_path)
    publish_period(load_version(interval, mart_path))
