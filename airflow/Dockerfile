FROM apache/airflow:3.4.0-python3.12

# Полный официальный образ уже содержит драйвер PostgreSQL.
RUN python -c "import psycopg2"

COPY --chown=airflow:root start.sh healthcheck.py /opt/airflow/
COPY --chown=airflow:root dags /opt/airflow/dags
COPY --chown=airflow:root tests /opt/airflow/tests

RUN mkdir -p /opt/airflow/state /opt/airflow/work
