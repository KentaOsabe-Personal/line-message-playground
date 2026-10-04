#!/bin/sh
# ローカルの静的チェック。修正や依存サービスの起動は行わない。
set -eu

cd "$(dirname "$0")/.."

check_frontend() {
  docker compose run --rm --no-deps frontend npm run check
}

check_backend() {
  docker compose run --rm --no-deps backend ruff check .
  docker compose run --rm --no-deps backend ruff format --check .
}

case "${1:-all}" in
  frontend) check_frontend ;;
  backend) check_backend ;;
  all) check_frontend; check_backend ;;
  *)
    echo "Usage: sh scripts/check.sh [all|frontend|backend]" >&2
    exit 2
    ;;
esac
