from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2AuthorizationCodeBearer

from .auth import Owner, TokenVerifier
from .clickhouse import ClickHouse
from .config import Settings
from .errors import ApiError
from .reports import Report, ReportPeriod, ReportService


def create_app(settings=None):
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="BionicPRO Reports API", version="1.0.0",
        description="Готовые дневные отчёты из ClickHouse. Границы периода включительны, время — UTC.",
        swagger_ui_init_oauth={"clientId": settings.frontend_client,
                               "usePkceWithAuthorizationCodeGrant": True,
                               "scopes": "openid"},
    )
    app.state.warehouse = ClickHouse(settings)
    app.state.verifier = TokenVerifier(settings)
    bearer = OAuth2AuthorizationCodeBearer(
        authorizationUrl=settings.issuer + "/protocol/openid-connect/auth",
        tokenUrl=settings.issuer + "/protocol/openid-connect/token",
        scopes={"openid": "Вход через Keycloak"}, auto_error=False,
    )

    def current_owner(request: Request, token: Annotated[str | None, Depends(bearer)]) -> Owner:
        return request.app.state.verifier.verify(token)

    app.add_middleware(
        CORSMiddleware, allow_origins=[settings.frontend_origin],
        allow_methods=["GET"], allow_headers=["Authorization"],
        expose_headers=["Content-Disposition"],
    )

    @app.middleware("http")
    async def response_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(exc.body, status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Не отражаем произвольные значения запроса в диагностике.
        fields = [{"field": ".".join(map(str, error["loc"])), "type": error["type"]}
                  for error in exc.errors()]
        return JSONResponse({"error": "invalid_request",
                             "message": "Укажите date_from и date_to в формате YYYY-MM-DD; максимум 31 день.",
                             "fields": fields}, status_code=422)

    @app.get("/health", tags=["Состояние"])
    def health(request: Request):
        request.app.state.warehouse.health()
        return {"status": "ok"}

    @app.get("/reports", response_model=Report, tags=["Отчёты"],
             summary="Получить собственный готовый отчёт",
             responses={401: {"description": "Нет действительного access-токена"},
                        409: {"description": "Хотя бы один день ещё не готов"},
                        413: {"description": "Слишком много строк: сократите период"},
                        503: {"description": "Зависимый сервис недоступен"}})
    def reports(request: Request, response: Response,
                owner: Annotated[Owner, Depends(current_owner)],
                period: Annotated[ReportPeriod, Query()]):
        report = ReportService(request.app.state.warehouse).get_report(owner, period)
        response.headers["Content-Disposition"] = (
            f'attachment; filename="bionicpro-report-{period.date_from}_{period.date_to}.json"'
        )
        return report

    return app
