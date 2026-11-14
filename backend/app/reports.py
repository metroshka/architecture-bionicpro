from datetime import date, datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .errors import ApiError, warehouse_unavailable


MAX_REPORT_DAYS = 31
MAX_REPORT_ROWS = 10000


class ReportPeriod(BaseModel):
    model_config = {"extra": "forbid"}

    date_from: date = Field(description="Первый день UTC, включительно", examples=["2026-11-08"])
    date_to: date = Field(description="Последний день UTC, включительно", examples=["2026-11-10"])

    @field_validator("date_from", "date_to", mode="before")
    @classmethod
    def iso_day(cls, value):
        if isinstance(value, str):
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError("Используйте YYYY-MM-DD")
        elif type(value) is not date:
            raise ValueError("Используйте YYYY-MM-DD")
        return value

    @model_validator(mode="after")
    def valid_range(self):
        if self.date_from < date(1970, 1, 1) or self.date_to < self.date_from:
            raise ValueError("Неверные границы периода")
        if (self.date_to - self.date_from).days >= MAX_REPORT_DAYS:
            raise ValueError(f"За один запрос доступно не более {MAX_REPORT_DAYS} дней")
        return self

    def days(self):
        return [self.date_from + timedelta(days=offset)
                for offset in range((self.date_to - self.date_from).days + 1)]


class ReportItem(BaseModel):
    report_date: date
    prosthesis_id: UUID
    model: str
    movement_count: int = Field(ge=0)
    avg_response_time_ms: float = Field(ge=0, allow_inf_nan=False)


class Report(BaseModel):
    status: Literal["ready", "empty"]
    timezone: Literal["UTC"] = "UTC"
    period: ReportPeriod
    row_count: int
    items: list[ReportItem]


class ReportService:
    def __init__(self, warehouse):
        self.warehouse = warehouse

    def get_report(self, owner, period, today=None):
        today = today or datetime.now(timezone.utc).date()
        parameters = {"date_from": period.date_from, "date_to": period.date_to}
        ready_rows = self.warehouse.select("""
            SELECT report_date, publication_id FROM reports.ready_periods
            WHERE report_date BETWEEN {date_from:Date} AND {date_to:Date}
            ORDER BY report_date
        """, parameters)
        try:
            ready = {date.fromisoformat(row["report_date"]): UUID(row["publication_id"])
                     for row in ready_rows}
        except (KeyError, ValueError, TypeError):
            raise warehouse_unavailable() from None

        days = period.days()
        missing = [day.isoformat() for day in days if day >= today or day not in ready]
        if missing:
            raise ApiError(409, "period_not_ready", "Не все дни периода обработаны Airflow.",
                           missing_dates=missing)

        # Снимок ссылок на версии из первого запроса: повторный запуск DAG
        # между двумя SELECT не подменит версии уже формируемого отчёта.
        pairs = []
        for index, day in enumerate(days):
            parameters[f"day_{index}"] = day
            parameters[f"version_{index}"] = ready[day]
            pairs.append(f"({{day_{index}:Date}}, {{version_{index}:UUID}})")
        parameters.update(owner_issuer=owner.issuer, owner_subject=owner.subject)
        rows = self.warehouse.select("""
            SELECT report_date, prosthesis_id, model, movement_count, avg_response_time_ms
            FROM reports.daily_prosthesis_usage
            WHERE owner_issuer = {owner_issuer:String} AND owner_subject = {owner_subject:String}
              AND report_date BETWEEN {date_from:Date} AND {date_to:Date}
              AND (report_date, publication_id) IN (""" + ", ".join(pairs) + ")"
            + f" ORDER BY report_date, prosthesis_id LIMIT {MAX_REPORT_ROWS + 1}", parameters)
        if len(rows) > MAX_REPORT_ROWS:
            raise ApiError(413, "report_too_large", "Сократите запрошенный период отчёта.")
        try:
            items = [ReportItem.model_validate(row) for row in rows]
        except ValidationError:
            raise warehouse_unavailable() from None
        return Report(status="ready" if items else "empty", period=period,
                      row_count=len(items), items=items)
