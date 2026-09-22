#!/bin/sh
# Barcha testlar (DB testlari ham) — hech narsa o'rnatmasdan, faqat Docker bilan.
# Vaqtinchalik PostgreSQL konteyneri ko'tariladi va oxirida o'chiriladi.
#   ./tools/test.sh            # backend testlari + web typecheck
#   ./tools/test.sh -k ldap    # pytest argumentlari
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
ID=agentmon-test-$$
NET=$ID-net

cleanup() { docker rm -f "$ID-pg" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

docker network create "$NET" >/dev/null
docker run -d --name "$ID-pg" --network "$NET" -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test postgres:16-alpine >/dev/null
until docker exec "$ID-pg" pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done

echo "== backend testlari"
docker run --rm --network "$NET" -v "$ROOT/backend:/src:ro" -w /src \
    -e AGENTMON_TEST_DSN="postgresql://postgres:test@$ID-pg:5432/test" -e PYTHONDONTWRITEBYTECODE=1 \
    python:3.12-slim sh -c "apt-get -qq update >/dev/null && apt-get -qq install -y openssl >/dev/null \
        && pip install -q --root-user-action=ignore -r requirements-dev.txt \
        && python -m pytest -q -p no:cacheprovider $*"

echo "== web typecheck"
docker run --rm -v "$ROOT/web:/src:ro" -w /tmp node:22-alpine sh -c \
    "cp -r /src/. /tmp/w && cd /tmp/w && npm ci --silent && npx tsc -b && echo 'tsc: xatosiz'"
