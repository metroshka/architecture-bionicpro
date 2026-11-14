import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.auth import TokenVerifier
from app.config import Settings
from app.errors import ApiError
from app.main import create_app


class TokenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.settings = Settings("http://clickhouse:8123", "reports_reader", "test",
                                "http://keycloak/realms/reports", "http://keycloak/certs")

    def setUp(self):
        self.verifier = TokenVerifier(self.settings)
        self.verifier.keys = Mock()
        self.verifier.keys.get_signing_key_from_jwt.return_value = SimpleNamespace(key=self.key.public_key())

    def token(self, omit=(), **overrides):
        now = int(time.time())
        claims = {"iss": self.settings.issuer, "sub": "owner-one", "aud": "reports-api",
                  "iat": now, "exp": now + 60, "typ": "Bearer", "azp": "reports-frontend"}
        claims.update(overrides)
        for name in omit:
            claims.pop(name)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "test-key"})

    def test_owner_comes_from_verified_token(self):
        owner = self.verifier.verify(self.token())
        self.assertEqual((owner.issuer, owner.subject), (self.settings.issuer, "owner-one"))

    def test_invalid_claims_and_expired_token_are_rejected(self):
        for claims in [{"aud": "unrelated-api"}, {"iss": "http://another-issuer"},
                       {"exp": 1}, {"typ": "ID"}, {"sub": ""}, {"azp": "other-client"}]:
            with self.subTest(claims=claims), self.assertRaises(ApiError) as error:
                self.verifier.verify(self.token(**claims))
            self.assertEqual(error.exception.status, 401)

    def test_signature_is_checked(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.verifier.keys.get_signing_key_from_jwt.return_value = SimpleNamespace(key=other.public_key())
        with self.assertRaises(ApiError) as error:
            self.verifier.verify(self.token())
        self.assertEqual(error.exception.status, 401)

    def test_missing_required_claims_are_rejected(self):
        for claim in ("iss", "sub", "aud", "exp", "iat", "typ", "azp"):
            with self.subTest(claim=claim), self.assertRaises(ApiError) as error:
                self.verifier.verify(self.token(omit=(claim,)))
            self.assertEqual(error.exception.status, 401)

    def test_unsigned_and_hmac_tokens_are_not_accepted(self):
        tokens = [
            jwt.encode({"sub": "owner-one"}, key="", algorithm="none"),
            jwt.encode({"sub": "owner-one"}, key="test-secret-for-negative-case-only", algorithm="HS256"),
        ]
        for token in tokens:
            with self.subTest(), self.assertRaises(ApiError) as error:
                self.verifier.verify(token)
            self.assertEqual(error.exception.status, 401)
        self.verifier.keys.get_signing_key_from_jwt.assert_not_called()

    def test_expired_token_is_rejected_before_reading_data(self):
        app = create_app(self.settings)
        app.state.verifier = self.verifier
        app.state.warehouse = Mock()
        response = TestClient(app).get(
            "/reports?date_from=2026-11-10&date_to=2026-11-10",
            headers={"Authorization": "Bearer " + self.token(exp=1)},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"], "unauthorized")
        app.state.warehouse.select.assert_not_called()

    def test_no_token_is_rejected_before_reading_data(self):
        app = create_app(self.settings)
        app.state.warehouse = Mock()
        result = TestClient(app).get("/reports?date_from=2026-11-10&date_to=2026-11-10")
        self.assertEqual(result.status_code, 401)
        self.assertEqual(result.headers["www-authenticate"], "Bearer")
        app.state.warehouse.select.assert_not_called()


if __name__ == "__main__":
    unittest.main()
