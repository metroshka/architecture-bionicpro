CREATE SCHEMA telemetry;

-- event_id идентифицирует движение, ingestion_id — приём сообщения.
-- Повторная доставка события может создать несколько строк с одним event_id.
CREATE TABLE telemetry.movement_events (
    ingestion_id UUID PRIMARY KEY,
    event_id UUID NOT NULL,
    prosthesis_id UUID NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    response_time_ms INTEGER NOT NULL CHECK (response_time_ms >= 0)
);
CREATE INDEX movement_events_time_idx ON telemetry.movement_events (occurred_at);
CREATE INDEX movement_events_event_idx ON telemetry.movement_events (event_id);

-- Принадлежность протеза проверяет ETL по отдельному источнику CRM.
CREATE ROLE telemetry_reader LOGIN PASSWORD 'telemetry_reader_demo';
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CONNECT ON DATABASE telemetry FROM PUBLIC;
GRANT CONNECT ON DATABASE telemetry TO telemetry_reader;
GRANT USAGE ON SCHEMA telemetry TO telemetry_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA telemetry TO telemetry_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA telemetry GRANT SELECT ON TABLES TO telemetry_reader;
ALTER ROLE telemetry_reader SET default_transaction_read_only = on;
