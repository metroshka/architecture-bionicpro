CREATE DATABASE IF NOT EXISTS reports;

-- Одна строка: владелец, протез, день и версия публикации.
-- Группировка и расчёт показателей выполняются DAG, а не API.
CREATE TABLE IF NOT EXISTS reports.daily_prosthesis_usage
(
    owner_issuer String,
    owner_subject String,
    client_id UUID,
    prosthesis_id UUID,
    model LowCardinality(String),
    report_date Date,
    movement_count UInt64,
    avg_response_time_ms Float64,
    publication_id UUID,
    loaded_at DateTime64(6, 'UTC') DEFAULT now64(6)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(report_date)
ORDER BY (owner_issuer, owner_subject, report_date, prosthesis_id, publication_id);

-- Записывается ПОСЛЕ успешной загрузки и проверки всей версии витрины.
-- Для пустого дня строка готовности существует, а строк витрины нет.
CREATE TABLE IF NOT EXISTS reports.report_publications
(
    report_date Date,
    publication_id UUID,
    published_at DateTime64(6, 'UTC'),
    row_count UInt64,
    event_count UInt64,
    airflow_run_id String
)
ENGINE = MergeTree
ORDER BY (report_date, published_at, publication_id);

-- Утверждённая версия выбирается одним argMax по (время, UUID),
-- а не по максимальной дате телеметрии и не по всем версиям сразу.
CREATE VIEW IF NOT EXISTS reports.ready_periods AS
SELECT
    report_date,
    argMax(publication_id, tuple(published_at, publication_id)) AS publication_id
FROM reports.report_publications
GROUP BY report_date;

-- Пароли демонстрационные; роли разделяют запись ETL и чтение API.
CREATE USER IF NOT EXISTS etl_writer IDENTIFIED WITH sha256_password BY 'etl_writer_demo';
GRANT SELECT, INSERT ON reports.daily_prosthesis_usage TO etl_writer;
GRANT SELECT, INSERT ON reports.report_publications TO etl_writer;
GRANT SELECT ON reports.ready_periods TO etl_writer;

CREATE USER IF NOT EXISTS reports_reader IDENTIFIED WITH sha256_password BY 'reports_reader_demo';
GRANT SELECT ON reports.daily_prosthesis_usage TO reports_reader;
GRANT SELECT ON reports.report_publications TO reports_reader;
GRANT SELECT ON reports.ready_periods TO reports_reader;
ALTER USER reports_reader SETTINGS readonly = 1;
