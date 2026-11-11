#!/usr/bin/env bash
set -euo pipefail

# Run inside the Keycloak container using the credentials from Docker Compose.
: "${KEYCLOAK_ADMIN:?KEYCLOAK_ADMIN is required}"
: "${KEYCLOAK_ADMIN_PASSWORD:?KEYCLOAK_ADMIN_PASSWORD is required}"

kcadm() {
  /opt/keycloak/bin/kcadm.sh "$@" --no-config \
    --server http://localhost:8080 --realm master \
    --user "$KEYCLOAK_ADMIN" --password "$KEYCLOAK_ADMIN_PASSWORD"
}

reports_client_id=$(kcadm get clients -r reports-realm \
  -q clientId=reports-frontend --fields id --format csv --noquotes)

if [[ -z "$reports_client_id" || "$reports_client_id" == *$'\n'* ]]; then
  printf '%s\n' 'Expected exactly one reports-frontend client in reports-realm.' >&2
  exit 1
fi

kcadm update "clients/$reports_client_id" -r reports-realm \
  -s standardFlowEnabled=true \
  -s implicitFlowEnabled=false \
  -s directAccessGrantsEnabled=false \
  -s 'attributes."pkce.code.challenge.method"="S256"'

kcadm get "clients/$reports_client_id" -r reports-realm \
  --fields 'clientId,publicClient,standardFlowEnabled,implicitFlowEnabled,directAccessGrantsEnabled,attributes(*)'
