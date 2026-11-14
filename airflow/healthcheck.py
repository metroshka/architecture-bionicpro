"""HTTP 200 недостаточно: проверяем состояние процессов в теле ответа."""

import json
from urllib.request import urlopen

with urlopen("http://localhost:8080/api/v2/monitor/health", timeout=5) as response:
    status = json.load(response)

if not all(status.get(name, {}).get("status") == "healthy"
           for name in ("metadatabase", "scheduler", "dag_processor")):
    raise SystemExit(1)
