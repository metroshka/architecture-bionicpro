# BionicPRO

Учебное приложение отчётов: React / TypeScript, Keycloak 21.1 и PostgreSQL для данных Keycloak.

## Управление доступом — задание 1

- [Архитектура в draw.io](docs/architecture/security.drawio): контейнеры системы, BFF, внешние каталоги и IdP, размещение данных по странам.
- [Настройки фронтенда](frontend/src/App.tsx): Authorization Code Flow с PKCE `S256`, реализованный библиотекой `keycloak-js` 21.1.2 из lock-файла.
- [Конфигурация realm](keycloak/realm-export.json): обязательный PKCE `S256` для публичного клиента `reports-frontend`. Standard Flow включён; Implicit Flow и Direct Access Grants отключены.
- [Применение PKCE к существующему клиенту](keycloak/enable-pkce.sh): обновление клиента через Admin CLI без повторного импорта realm. Скрипт можно запускать повторно.

В целевой архитектуре BFF получает и хранит токены IdP на сервере, а браузер использует защищённую cookie с идентификатором сессии. Каждая страна имеет собственные Keycloak, источники пользователей и хранилища.

Практическая реализация задачи 1.2 использует исходную схему React → Keycloak: токены получает JavaScript-адаптер во фронтенде. PKCE защищает обмен authorization code и не переносит хранение токенов на сервер. BFF и внешние источники пользователей показаны в архитектуре, но не входят в текущий Docker Compose.

## Локальный запуск

Команды выполняются в терминале из корня репозитория. Нужны запущенный Docker Desktop и Docker Compose.

```bash
docker compose up -d --build
docker compose ps -a
```

Приложение: [http://localhost:3000](http://localhost:3000). Keycloak: [http://localhost:8080](http://localhost:8080). Учебные пользователи заданы в `keycloak/realm-export.json`.

Адреса фронтенда берутся из `frontend/.env` во время сборки. После изменения кода или этого файла требуется пересборка образа:

```bash
docker compose up -d --build frontend
```

### Если realm уже существовал

При старте с `--import-realm` Keycloak пропускает существующий realm. Изменение JSON и перезапуск контейнера сами по себе не обновят настройки клиента в БД.

После готовности Keycloak выполните:

```bash
docker compose exec -T keycloak bash -s < keycloak/enable-pkce.sh
```

Скрипт использует `KEYCLOAK_ADMIN` и `KEYCLOAK_ADMIN_PASSWORD` из окружения контейнера. Admin CLI работает с `--no-config`, поэтому его токены не сохраняются в конфигурационном файле. Обновляется только клиент `reports-frontend` в `reports-realm`; пользователи и остальные настройки realm сохраняются.

В результате должны выводиться:

```json
{
  "clientId": "reports-frontend",
  "publicClient": true,
  "standardFlowEnabled": true,
  "implicitFlowEnabled": false,
  "directAccessGrantsEnabled": false,
  "attributes": {
    "pkce.code.challenge.method": "S256"
  }
}
```

В `attributes` могут присутствовать дополнительные настройки клиента.

## Проверка PKCE

### Успешный вход

1. Откройте новое приватное окно браузера и перейдите на `http://localhost:3000`.
2. Откройте инструменты разработчика → Network и включите сохранение запросов при переходах между страницами (Preserve log).
3. Нажмите Login. В запросе к `/realms/reports-realm/protocol/openid-connect/auth` проверьте `response_type=code`, наличие `code_challenge` и `code_challenge_method=S256`.
4. Войдите под учебным пользователем. В POST-запросе к `/realms/reports-realm/protocol/openid-connect/token` должны присутствовать `grant_type=authorization_code` и `code_verifier`. Ожидается HTTP 200 и страница Usage Reports.

Адаптер создаёт новый verifier для каждого входа и передаёт его при обмене кода. Секрет клиента для публичного веб-клиента не используется. Токены, пароль, authorization code и verifier не следует включать в скриншоты или публиковать в репозитории.

### Отказ при отсутствии PKCE

Из корня проекта выполните запрос без PKCE-параметров. Команда не следует по перенаправлению и выводит заголовки ответа:

```bash
curl -sS --max-time 15 -D - -o /dev/null --get \
  'http://localhost:8080/realms/reports-realm/protocol/openid-connect/auth' \
  --data-urlencode 'client_id=reports-frontend' \
  --data-urlencode 'redirect_uri=http://localhost:3000/' \
  --data-urlencode 'response_type=code' \
  --data-urlencode 'response_mode=query' \
  --data-urlencode 'scope=openid' \
  --data-urlencode 'state=pkce-negative-test'
```

Ожидается перенаправление с `error=invalid_request` и описанием отсутствующего PKCE-параметра в `Location`. Показ страницы входа вместо ошибки означает, что PKCE ещё не обязателен для клиента. Один успешный вход без проверки параметров и отказа не подтверждает настройку PKCE.

### Результат локальной проверки

| Проверка | Подтверждённый результат |
|---|---|
| Сборка и запуск фронтенда | Образ пересобран, контейнер запущен; приложение отвечает HTTP 200. |
| Настройки работающего Keycloak | Для `reports-frontend` установлен обязательный метод `S256`; Standard Flow включён, Implicit Flow и Direct Access Grants отключены. |
| Запрос авторизации | Передаются `code_challenge` и `code_challenge_method=S256`. |
| Обмен кода на токены | POST `/token` содержит `grant_type=authorization_code` и `code_verifier`; получен HTTP 200. |
| Запрос авторизации без PKCE | Получен HTTP 302 с `error=invalid_request` и `error_description=Missing parameter: code_challenge_method` в `Location`. |

Кнопка Download Report относится к заданию 2: текущая конфигурация Compose не содержит сервиса отчётов, Airflow и ClickHouse.

## Документация

- [Keycloak 21.1.2: JavaScript-адаптер и параметр pkceMethod](https://www.keycloak.org/docs/21.1.2/securing_apps/index.html#javascript-adapter-reference).
- [Keycloak 21.1.2: настройки клиента и Admin CLI](https://www.keycloak.org/docs/21.1.2/server_admin/index.html).
- [Импорт существующих realm при запуске](https://www.keycloak.org/server/importExport).
- [RFC 7636: Proof Key for Code Exchange](https://www.rfc-editor.org/rfc/rfc7636.html).
