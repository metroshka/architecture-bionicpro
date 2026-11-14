-- Учебный SQL-источник CRM. Таблицы и поля выбраны для проекта,
-- это не схема Битрикс24. Данные загружает scripts/seed_demo.py.
CREATE SCHEMA crm;

CREATE TABLE crm.clients (
    client_id UUID PRIMARY KEY,
    owner_issuer TEXT NOT NULL CHECK (owner_issuer <> ''),
    owner_subject TEXT NOT NULL CHECK (owner_subject <> ''),
    UNIQUE (owner_issuer, owner_subject)
);

CREATE TABLE crm.prostheses (
    prosthesis_id UUID PRIMARY KEY,
    client_id UUID NOT NULL REFERENCES crm.clients(client_id),
    model TEXT NOT NULL CHECK (model <> '')
);
CREATE INDEX prostheses_client_idx ON crm.prostheses (client_id);

-- Доступ DAG: только чтение источника.
CREATE ROLE crm_reader LOGIN PASSWORD 'crm_reader_demo';
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CONNECT ON DATABASE crm FROM PUBLIC;
GRANT CONNECT ON DATABASE crm TO crm_reader;
GRANT USAGE ON SCHEMA crm TO crm_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA crm TO crm_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA crm GRANT SELECT ON TABLES TO crm_reader;
ALTER ROLE crm_reader SET default_transaction_read_only = on;
