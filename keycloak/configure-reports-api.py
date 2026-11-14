#!/usr/bin/env python3
"""Добавляет audience API и вход из Swagger UI в существующий клиент Keycloak."""

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REALM = "reports-realm"
CLIENT = "reports-frontend"


def kcadm(*args, body=None):
    # Учётные данные администратора берутся только из контейнера.
    command = ["docker", "compose", "exec", "-T", "keycloak", "bash", "-c",
               'exec /opt/keycloak/bin/kcadm.sh "$@" --no-config '
               '--server http://localhost:8080 --realm master '
               '--user "$KEYCLOAK_ADMIN" --password "$KEYCLOAK_ADMIN_PASSWORD"',
               "kcadm", *args]
    result = subprocess.run(command, cwd=ROOT, text=True,
                            input=json.dumps(body) if body is not None else None,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise SystemExit("Не удалось выполнить Admin CLI Keycloak. Проверьте готовность контейнера и учётные данные администратора.")
    return json.loads(result.stdout) if args[0] == "get" else None


def main():
    template = json.loads((ROOT / "keycloak/realm-export.json").read_text())
    frontend = next(c for c in template["clients"] if c["clientId"] == CLIENT)
    mapper = next(m for m in frontend["protocolMappers"] if m["name"] == "reports-api-audience")
    clients = kcadm("get", "clients", "-r", REALM, "-q", "clientId=" + CLIENT,
                    "--fields", "id,clientId")
    if len(clients) != 1:
        raise SystemExit("Ожидается ровно один клиент reports-frontend.")
    path = "clients/" + clients[0]["id"]
    current = kcadm("get", path, "-r", REALM, "--fields", "redirectUris,webOrigins")
    # Дополняем адреса; ранее настроенные адреса не теряются.
    redirects = list(dict.fromkeys(current.get("redirectUris", [])
                                  + ["http://localhost:8000/docs/oauth2-redirect"]))
    origins = list(dict.fromkeys(current.get("webOrigins", []) + ["http://localhost:8000"]))
    kcadm("update", path, "-r", REALM, "-f", "-",
          body={"redirectUris": redirects, "webOrigins": origins})
    mappers_path = path + "/protocol-mappers/models"
    existing = [m for m in kcadm("get", mappers_path, "-r", REALM)
                if m["name"] == mapper["name"]]
    if len(existing) > 1:
        raise SystemExit("Обнаружено несколько одноимённых audience mapper; требуется проверка настройки.")
    if existing:
        kcadm("update", mappers_path + "/" + existing[0]["id"], "-r", REALM, "-f", "-",
              body={**mapper, "id": existing[0]["id"]})
    else:
        kcadm("create", mappers_path, "-r", REALM, "-f", "-", body=mapper)
    actual = kcadm("get", mappers_path, "-r", REALM)
    if not any(m["name"] == mapper["name"] and m["config"] == mapper["config"] for m in actual):
        raise SystemExit("Keycloak не подтвердил настройки audience.")
    print("Keycloak: access-токены reports-frontend включают audience reports-api.")
    print("Разрешён вход с PKCE из http://localhost:8000/docs. Войдите заново для получения нового токена.")


if __name__ == "__main__":
    main()
