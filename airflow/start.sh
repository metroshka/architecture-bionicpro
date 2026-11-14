#!/usr/bin/env bash
set -euo pipefail
umask 077

# Процессы standalone наследуют один ключ; он сохраняется между перезапусками.
python - <<'PY'
from pathlib import Path
import secrets

state = Path('/opt/airflow/state')
state.mkdir(parents=True, exist_ok=True)
Path('/opt/airflow/work').mkdir(parents=True, exist_ok=True)
key_file = state / 'jwt_secret'
if not key_file.exists():
    with key_file.open('x') as stream:
        stream.write(secrets.token_hex(32))
PY
export AIRFLOW__API_AUTH__JWT_SECRET="$(cat /opt/airflow/state/jwt_secret)"
exec airflow standalone
