import json
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .errors import warehouse_unavailable


class ClickHouse:
    """Параметризованное чтение HTTP-интерфейса от имени reports_reader."""

    def __init__(self, settings):
        self.settings = settings

    def select(self, query, parameters=None):
        args = {"wait_end_of_query": "1"}
        args.update({"param_" + name: str(value)
                     for name, value in (parameters or {}).items()})
        request = Request(
            self.settings.clickhouse_url + "/?" + urlencode(args),
            data=(query + " FORMAT JSONEachRow").encode(), method="POST",
            headers={"X-ClickHouse-User": self.settings.clickhouse_user,
                     "X-ClickHouse-Key": self.settings.clickhouse_password,
                     "Content-Type": "text/plain; charset=utf-8"},
        )
        try:
            with urlopen(request, timeout=15) as response:
                # Не выдаём частичный успешный ответ при большом результате.
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise warehouse_unavailable()
            return [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
        except (URLError, OSError, ValueError):
            # Ответ ClickHouse может содержать SQL и данные: наружу не передаём.
            raise warehouse_unavailable() from None

    def health(self):
        self.select("SELECT report_date FROM reports.ready_periods LIMIT 0")
        self.select("SELECT report_date FROM reports.daily_prosthesis_usage LIMIT 0")
