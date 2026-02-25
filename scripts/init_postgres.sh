#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load .env file if it exists (same way docker-compose does)
# This ensures we use the same password that docker-compose used
if [[ -f "$ROOT_DIR/.env" ]]; then
  # Export variables from .env file
  set -a
  source "$ROOT_DIR/.env"
  set +a
fi

SQL_DIR="${SQL_DIR:-$ROOT_DIR/src/database/postgres/sql}"
SQL_ENTRY="${SQL_ENTRY:-elripley.sql}"
PSQL_FLAGS=(--echo-all --echo-errors -v ON_ERROR_STOP=1 -v VERBOSITY=verbose --pset pager=off)

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.infra.yml}"
NETWORK="${COMPOSE_NETWORK:-ai_agent_network}"
POSTGRES_SERVICE="${POSTGRES_SERVICE:-postgres}"

# When connecting from within Docker network, always use service name and internal port
# POSTGRES_HOST and POSTGRES_PORT from .env are for external connections (from host machine)
# We override them here to use Docker service name and internal port
POSTGRES_HOST="postgres"   # Always use service name in docker network
POSTGRES_PORT="5432"       # Always use internal port (5432), not external port (5434)
POSTGRES_USER="${POSTGRES_USER:-el-ripley-user}"
POSTGRES_DB_NAME="${POSTGRES_DB_NAME:-el_ripley}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-crypto-gambling-pass}"
POSTGRES_AGENT_READER_PASSWORD="${POSTGRES_AGENT_READER_PASSWORD:-agent-reader-dev-password}"
POSTGRES_AGENT_WRITER_PASSWORD="${POSTGRES_AGENT_WRITER_PASSWORD:-agent-writer-dev-password}"
POSTGRES_SUGGEST_RESPONSE_READER_PASSWORD="${POSTGRES_SUGGEST_RESPONSE_READER_PASSWORD:-suggest-response-reader-dev-password}"
POSTGRES_SUGGEST_RESPONSE_WRITER_PASSWORD="${POSTGRES_SUGGEST_RESPONSE_WRITER_PASSWORD:-suggest-response-writer-dev-password}"

PSQL_IMAGE="${PSQL_IMAGE:-postgres:16.3}"
PSQL_CONN_ARGS=(-h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB_NAME")

compose_cmd=(docker compose -f "$ROOT_DIR/$COMPOSE_FILE")

if ! "${compose_cmd[@]}" ps --services >/dev/null 2>&1; then
  echo "✖ Cannot access compose file $COMPOSE_FILE under $ROOT_DIR" >&2
  exit 1
fi

if [[ -z $("${compose_cmd[@]}" ps -q "$POSTGRES_SERVICE") ]]; then
  echo "✖ Postgres service '$POSTGRES_SERVICE' is not running. Start it first:"
  echo "    docker compose -f $COMPOSE_FILE up -d $POSTGRES_SERVICE"
  exit 1
fi

run_psql() {
  docker run --rm \
    --network "$NETWORK" \
    -e "PGPASSWORD=$POSTGRES_PASSWORD" \
    "$PSQL_IMAGE" \
    psql "${PSQL_FLAGS[@]}" "${PSQL_CONN_ARGS[@]}" "$@"
}

run_psql_quiet() {
  docker run --rm \
    --network "$NETWORK" \
    -e "PGPASSWORD=$POSTGRES_PASSWORD" \
    "$PSQL_IMAGE" \
    psql "${PSQL_CONN_ARGS[@]}" "$@"
}

echo "→ Checking existing schema..."
if [[ "${FORCE_INIT:-0}" != "1" && $(run_psql_quiet -tAc "SELECT to_regclass('public.users')") != "" ]]; then
  echo "Schema already exists. Nothing to do."
  echo "Set FORCE_INIT=1 to re-run anyway."
  exit 0
fi

echo "→ Ensuring pgcrypto extension..."
run_psql -c 'CREATE EXTENSION IF NOT EXISTS "pgcrypto";'

if [[ ! -d "$SQL_DIR" ]]; then
  echo "✖ SQL directory not found: $SQL_DIR" >&2
  exit 1
fi

echo "→ Applying SQL files from $SQL_DIR/$SQL_ENTRY"
docker run --rm \
  --network "$NETWORK" \
  -e "PGPASSWORD=$POSTGRES_PASSWORD" \
  -v "$SQL_DIR":/sql \
  -w /sql \
  "$PSQL_IMAGE" \
  psql "${PSQL_FLAGS[@]}" "${PSQL_CONN_ARGS[@]}" \
    -v dbname="$POSTGRES_DB_NAME" \
    -v agent_reader_password="'$POSTGRES_AGENT_READER_PASSWORD'" \
    -v agent_writer_password="'$POSTGRES_AGENT_WRITER_PASSWORD'" \
    -v suggest_response_reader_password="'$POSTGRES_SUGGEST_RESPONSE_READER_PASSWORD'" \
    -v suggest_response_writer_password="'$POSTGRES_SUGGEST_RESPONSE_WRITER_PASSWORD'" \
    -f "$SQL_ENTRY"

echo "✅ Database bootstrap completed."

