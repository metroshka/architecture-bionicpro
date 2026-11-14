#!/usr/bin/env python3
"""Проверить доступ к отчётам на локальном стенде с учебными user1/user2."""

import argparse
import base64
import hashlib
import json
import secrets
import sys
from datetime import date, timedelta
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import (HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler,
                            Request, build_opener)
from uuid import UUID, uuid5


ROOT = Path(__file__).resolve().parents[1]
KEYCLOAK = "http://localhost:8080"
ISSUER = KEYCLOAK + "/realms/reports-realm"
OIDC = ISSUER + "/protocol/openid-connect"
API = "http://localhost:8000"
CLIENT = "reports-frontend"
CALLBACK = "http://localhost:3000/"
NAMESPACE = UUID("0ac77251-bf19-48d6-a588-35d68b839826")


class CheckFailed(Exception):
    """Сообщения не содержат токенов, паролей или тела ответа Keycloak."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def http(opener, url, *, form=None, headers=None):
    # Скрипт предназначен только для данного локального стенда.
    origin = urlsplit(url)
    if (origin.scheme, origin.netloc) not in {("http", "localhost:8080"),
                                             ("http", "localhost:8000")}:
        raise CheckFailed("Запрос за пределы локального стенда запрещён.")
    request = Request(url, data=urlencode(form).encode() if form is not None else None,
                      headers=headers or {})
    if form is not None:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with opener.open(request, timeout=15) as response:
            return response.status, response.headers, response.read()
    except HTTPError as response:
        with response:
            return response.code, response.headers, response.read()
    except (URLError, OSError):
        raise CheckFailed("Локальный сервис недоступен. Проверьте api и keycloak в Docker Compose.") from None


def json_body(raw):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise CheckFailed("Сервис вернул ответ, который не является JSON.") from None
    if not isinstance(value, dict):
        raise CheckFailed("Сервис вернул неожиданную структуру JSON.")
    return value


def b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.action = None
        self.hidden = {}

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "form" and fields.get("id") == "kc-form-login":
            self.active = True
            self.action = fields.get("action")
        if self.active and tag == "input" and fields.get("type") == "hidden" and fields.get("name"):
            self.hidden[fields["name"]] = fields.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.active = False


class Login:
    """Отдельные cookies и новая сессия для каждого учебного пользователя."""

    def __init__(self, username):
        self.username = username
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()), NoRedirect())
        self.tokens = {}
        self.subject = None

    def authenticate(self, password):
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        challenge = b64url(hashlib.sha256(verifier.encode()).digest())
        url = OIDC + "/auth?" + urlencode({
            "client_id": CLIENT, "redirect_uri": CALLBACK, "response_type": "code",
            "response_mode": "query", "scope": "openid profile", "state": state,
            "prompt": "login", "code_challenge": challenge, "code_challenge_method": "S256",
        })
        status, _, raw = http(self.opener, url)
        if status != 200:
            raise CheckFailed("Keycloak не открыл форму входа с PKCE. Проверьте настройки reports-frontend.")
        form = LoginForm()
        form.feed(raw.decode("utf-8"))
        action = urlsplit(form.action or "")
        if ((action.scheme, action.netloc) != ("http", "localhost:8080")
                or not action.path.startswith("/realms/reports-realm/login-actions/authenticate")):
            raise CheckFailed("Форма входа отличается от ожидаемой формы учебного Keycloak 21.1.")
        status, headers, _ = http(self.opener, form.action,
                                  form={**form.hidden, "username": self.username, "password": password})
        target = urlsplit(headers.get("Location", ""))
        callback = urlsplit(CALLBACK)
        if status != 302 or (target.scheme, target.netloc, target.path) != (
                callback.scheme, callback.netloc, callback.path):
            raise CheckFailed("Вход " + self.username + " не завершён. Проверьте логин, пароль и обязательные действия при входе в Keycloak.")
        query = parse_qs(target.query)
        if query.get("state") != [state] or len(query.get("code", [])) != 1:
            raise CheckFailed("Keycloak вернул некорректный state или authorization code.")
        # Перенаправление на frontend не выполняется: code обменивается здесь.
        status, _, raw = http(self.opener, OIDC + "/token", form={
            "grant_type": "authorization_code", "client_id": CLIENT,
            "redirect_uri": CALLBACK, "code": query["code"][0], "code_verifier": verifier,
        })
        if status != 200:
            raise CheckFailed("Keycloak отклонил обмен кода с PKCE.")
        self.tokens = json_body(raw)
        if not all(isinstance(self.tokens.get(name), str) and self.tokens[name]
                   for name in ("access_token", "id_token", "refresh_token")):
            raise CheckFailed("Keycloak вернул неполный набор токенов.")
        status, _, raw = http(self.opener, OIDC + "/userinfo",
                              headers={"Authorization": "Bearer " + self.tokens["access_token"]})
        user = json_body(raw)
        if status != 200 or user.get("preferred_username") != self.username or not user.get("sub"):
            raise CheckFailed("Keycloak не подтвердил ожидаемую учебную учётную запись.")
        self.subject = user["sub"]

    def close(self):
        if self.tokens.get("refresh_token"):
            try:
                status, _, _ = http(self.opener, OIDC + "/logout", form={
                    "client_id": CLIENT, "refresh_token": self.tokens["refresh_token"],
                })
                if status != 204:
                    print("Внимание: Keycloak не подтвердил завершение тестовой сессии " + self.username + ".")
            except CheckFailed:
                print("Внимание: не удалось завершить тестовую сессию " + self.username + ".")
        self.tokens.clear()


def stable_id(name):
    return str(uuid5(NAMESPACE, name))


def expected_rows(day):
    earlier = (day - timedelta(days=2)).isoformat()
    day = day.isoformat()
    return {
        "user1": [(earlier, stable_id("prosthesis:1"), "DEMO-Hand-A", 2, 80),
                  (day, stable_id("prosthesis:1"), "DEMO-Hand-A", 3, 100),
                  (day, stable_id("prosthesis:2"), "DEMO-Hand-B", 2, 70)],
        "user2": [(earlier, stable_id("prosthesis:3"), "DEMO-Hand-A", 1, 105),
                  (day, stable_id("prosthesis:3"), "DEMO-Hand-A", 3, 100)],
    }


def changed_subject(token, subject):
    header, payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["sub"] = subject
    return header + "." + b64url(json.dumps(claims).encode()) + "." + signature


def require_status(label, result, status, error=None):
    actual, headers, body = result
    if actual != status or (error and body.get("error") != error):
        raise CheckFailed(f"{label}: ожидался HTTP {status}, получен HTTP {actual}.")
    if status != 200 and "items" in body:
        raise CheckFailed(label + ": ответ отказа содержит строки отчёта.")
    if status == 401 and headers.get("WWW-Authenticate", "").lower() != "bearer":
        raise CheckFailed(label + ": отсутствует WWW-Authenticate: Bearer.")
    print(f"OK: {label} — HTTP {status}.")


def verify_report(label, result, expected, dates):
    require_status(label, result, 200)
    body = result[2]
    try:
        rows = [(r["report_date"], r["prosthesis_id"], r["model"],
                 r["movement_count"], r["avg_response_time_ms"]) for r in body["items"]]
        valid = (body["status"] == "ready" and body["timezone"] == "UTC"
                 and body["period"] == dates and body["row_count"] == len(expected)
                 and sorted(rows) == sorted(expected))
    except (KeyError, TypeError):
        valid = False
    if not valid:
        raise CheckFailed(label + ": строки отличаются от собственных учебных данных. Проверьте seed и DAG.")
    print(f"    Собственные строки: {len(rows)}, движения: {sum(row[3] for row in rows)}; чужих протезов нет.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", type=date.fromisoformat, required=True,
                        help="последний день учебного набора, например 2026-11-10")
    args = parser.parse_args()
    dates = {"date_from": (args.day - timedelta(days=2)).isoformat(), "date_to": args.day.isoformat()}
    expected = expected_rows(args.day)
    realm = json.loads((ROOT / "keycloak/realm-export.json").read_text())
    users = {u["username"]: u for u in realm["users"]}
    passwords = {name: next(c["value"] for c in users[name]["credentials"] if c["type"] == "password")
                 for name in ("user1", "user2")}
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def report(token=None, extra=None, extra_headers=None):
        headers = dict(extra_headers or {})
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        status, response_headers, raw = http(opener, API + "/reports?" + urlencode({**dates, **(extra or {})}),
                                             headers=headers)
        return status, response_headers, json_body(raw)

    require_status("Без токена", report(), 401, "unauthorized")
    require_status("Некорректный токен", report("not-a-jwt"), 401, "unauthorized")
    sessions = [Login("user1"), Login("user2")]
    try:
        for session in sessions:
            session.authenticate(passwords[session.username])
            print("OK: вход " + session.username + " через Authorization Code + PKCE S256.")
        if sessions[0].subject == sessions[1].subject:
            raise CheckFailed("Учебные пользователи получили одинаковый subject.")
        for session, other in ((sessions[0], sessions[1]), (sessions[1], sessions[0])):
            name = session.username
            token = session.tokens["access_token"]
            verify_report(name, report(token), expected[name], dates)
            selectors = {"user_id": other.subject, "owner_subject": other.subject,
                         "owner_issuer": "http://another-issuer.invalid",
                         "client_id": stable_id("client:" + other.username),
                         "prosthesis_id": expected[other.username][0][1]}
            for parameter, value in selectors.items():
                require_status(name + ": подмена " + parameter,
                               report(token, extra={parameter: value}), 422, "invalid_request")
            verify_report(name + ": подмена заголовков владельца",
                          report(token, extra_headers={"X-User-Id": other.subject,
                                                       "X-Owner-Subject": other.subject}), expected[name], dates)
            require_status(name + ": изменённый sub в JWT",
                           report(changed_subject(token, other.subject)), 401, "unauthorized")
            require_status(name + ": ID-токен вместо access-токена",
                           report(session.tokens["id_token"]), 401, "unauthorized")
            require_status(name + ": refresh-токен вместо access-токена",
                           report(session.tokens["refresh_token"]), 401, "unauthorized")
    finally:
        for session in sessions:
            session.close()
    print("Проверки доступа пройдены: user1 и user2 получают только свои отчёты.")


if __name__ == "__main__":
    try:
        main()
    except CheckFailed as error:
        print("ОШИБКА: " + str(error), file=sys.stderr)
        raise SystemExit(1)
    except (KeyError, ValueError, TypeError, OSError):
        print("ОШИБКА: проверьте учебный realm-export.json и формат ответов локального стенда.", file=sys.stderr)
        raise SystemExit(1)
