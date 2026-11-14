import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    clickhouse_url: str
    clickhouse_user: str
    clickhouse_password: str = field(repr=False)
    issuer: str
    jwks_url: str
    audience: str = "reports-api"
    frontend_client: str = "reports-frontend"
    frontend_origin: str = "http://localhost:3000"

    @classmethod
    def from_env(cls):
        return cls(
            clickhouse_url=os.environ["REPORTS_CLICKHOUSE_URL"].rstrip("/"),
            clickhouse_user=os.environ["REPORTS_CLICKHOUSE_USER"],
            clickhouse_password=os.environ["REPORTS_CLICKHOUSE_PASSWORD"],
            issuer=os.environ["REPORTS_OIDC_ISSUER"].rstrip("/"),
            jwks_url=os.environ["REPORTS_OIDC_JWKS_URL"],
            audience=os.environ.get("REPORTS_OIDC_AUDIENCE", "reports-api"),
            frontend_client=os.environ.get("REPORTS_OIDC_CLIENT", "reports-frontend"),
            frontend_origin=os.environ.get("REPORTS_FRONTEND_ORIGIN", "http://localhost:3000"),
        )
