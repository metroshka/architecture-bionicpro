from dataclasses import dataclass

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError

from .errors import ApiError


@dataclass(frozen=True)
class Owner:
    issuer: str
    subject: str


class TokenVerifier:
    def __init__(self, settings):
        self.settings = settings
        # URL ключей задаёт конфигурация сервиса, а не содержимое токена.
        self.keys = PyJWKClient(settings.jwks_url, cache_keys=False,
                                cache_jwk_set=True, lifespan=300, timeout=5)

    def verify(self, token):
        if not token or len(token) > 16384:
            raise self.unauthorized()
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise self.unauthorized()
            key = self.keys.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, key.key, algorithms=["RS256"],
                issuer=self.settings.issuer, audience=self.settings.audience,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "typ", "azp"]},
            )
            if (claims["typ"] != "Bearer"
                    or claims["azp"] != self.settings.frontend_client
                    or not isinstance(claims["sub"], str) or not claims["sub"].strip()):
                raise self.unauthorized()
            return Owner(claims["iss"], claims["sub"])
        except PyJWKClientConnectionError:
            raise ApiError(503, "identity_provider_unavailable",
                           "Не удалось получить ключи проверки входа. Повторите запрос позже.") from None
        except jwt.PyJWTError:
            raise self.unauthorized() from None

    @staticmethod
    def unauthorized():
        return ApiError(401, "unauthorized", "Требуется действительный access-токен Keycloak.")
