#!/usr/bin/env bash
# Despliega una versión del backend en el VPS (INFRA-03, ADR-006).
#
# Lo corre el workflow de CD por SSH dentro del directorio del entorno
# (p. ej. /opt/onecreator/staging), que tiene:
#   docker-compose.prod.yml   copiado por el workflow en cada deploy
#   deploy.sh                 este script, copiado igual
#   .env                      variables del compose: POSTGRES_PASSWORD, WEB_PORT,
#                             COMPOSE_PROJECT_NAME, SMOKE_HOST
#   back/.env.prod            variables de Django (SECRET_KEY, ALLOWED_HOSTS, R2, ...)
#
# Uso: ./deploy.sh <imagen web> <imagen worker>
#   p. ej. ./deploy.sh ghcr.io/romekai/onecreator-web:sha-abc1234 ghcr.io/romekai/onecreator-worker:sha-abc1234
#
# Pasos: pull de las imágenes, up sin build, smoke test contra /api/v1/status/.
# Si el smoke test falla vuelve a las imágenes del deploy anterior y sale con 1.
# Las migraciones no se revierten: tienen que ser compatibles con la versión anterior.
# Ver documents/runbooks/deploy-vps.md.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "uso: $0 <imagen web> <imagen worker>" >&2
  exit 2
fi

cd "$(dirname "$0")"

COMPOSE_FILE=docker-compose.prod.yml
STATE_FILE=.deploy-state          # imágenes del último deploy que pasó el smoke test
SMOKE_TIMEOUT="${SMOKE_TIMEOUT:-120}"

for required in "$COMPOSE_FILE" .env back/.env.prod; do
  if [ ! -f "$required" ]; then
    echo "deploy: falta $PWD/$required (ver documents/runbooks/deploy-vps.md)" >&2
    exit 2
  fi
done

# WEB_PORT y SMOKE_HOST salen del .env del entorno. Se leen sin hacer source:
# el .env es formato de compose, no de shell (una contraseña con $ o espacios lo rompería).
env_value() {
  sed -n "s/^$1=//p" .env | tail -n 1 | sed -e 's/^["'\'']//' -e 's/["'\'']$//'
}
WEB_PORT="$(env_value WEB_PORT)"
WEB_PORT="${WEB_PORT:-8000}"
SMOKE_HOST="$(env_value SMOKE_HOST)"
SMOKE_HOST="${SMOKE_HOST:-localhost}"

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

smoke_test() {
  # La API solo escucha en 127.0.0.1. Host tiene que estar en ALLOWED_HOSTS y
  # X-Forwarded-Proto evita el redirect a HTTPS, como si viniera del reverse proxy.
  local url="http://127.0.0.1:${WEB_PORT}/api/v1/status/"
  local deadline=$((SECONDS + SMOKE_TIMEOUT))
  echo "deploy: smoke test contra $url (Host: $SMOKE_HOST, hasta ${SMOKE_TIMEOUT}s)"
  while [ "$SECONDS" -lt "$deadline" ]; do
    if curl -fsS -o /dev/null --max-time 5 \
         -H "Host: $SMOKE_HOST" -H "X-Forwarded-Proto: https" "$url"; then
      if [ -n "$(compose ps --status running -q worker)" ]; then
        echo "deploy: smoke test OK"
        return 0
      fi
    fi
    sleep 5
  done
  echo "deploy: smoke test falló" >&2
  compose ps >&2 || true
  compose logs --tail 50 web worker >&2 || true
  return 1
}

start() {
  export WEB_IMAGE="$1" WORKER_IMAGE="$2"
  compose pull web worker beat
  compose up -d --no-build --remove-orphans
}

PREVIOUS_WEB=""
PREVIOUS_WORKER=""
if [ -f "$STATE_FILE" ]; then
  read -r PREVIOUS_WEB PREVIOUS_WORKER < "$STATE_FILE" || true
fi

echo "deploy: desplegando $1 y $2"
start "$1" "$2"

if smoke_test; then
  printf '%s %s\n' "$1" "$2" > "$STATE_FILE"
  docker image prune -f >/dev/null || true
  echo "deploy: listo"
  exit 0
fi

if [ -z "$PREVIOUS_WEB" ] || [ -z "$PREVIOUS_WORKER" ]; then
  echo "deploy: no hay un deploy anterior al que volver" >&2
  exit 1
fi

echo "deploy: rollback a $PREVIOUS_WEB y $PREVIOUS_WORKER" >&2
start "$PREVIOUS_WEB" "$PREVIOUS_WORKER"
if smoke_test; then
  echo "deploy: rollback OK, sigue corriendo la versión anterior" >&2
else
  echo "deploy: el rollback tampoco pasó el smoke test, revisar a mano" >&2
fi
exit 1
