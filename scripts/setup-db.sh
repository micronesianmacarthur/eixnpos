#!/usr/bin/env bash
#
# setup-db.sh - initialize/reset the eixnpos database.
#
# Applies Django migrations against backend/db.sqlite3. On a genuinely fresh
# database in development (DEBUG), the accounts app's post_migrate hook seeds a
# default admin/admin user (must_change_password=True) automatically.
#
# Usage:
#   scripts/setup-db.sh            migrate the database (create if missing)
#   scripts/setup-db.sh --fresh    delete backend/db.sqlite3, then migrate
#   scripts/setup-db.sh --seed-business
#                                  also seed default BusinessInfo values
#                                  (runs accounts.seed_business_admin)
#
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$ROOT/backend/db.sqlite3"
MANAGE=(uv run python "$ROOT/backend/manage.py")

usage() {
  cat <<'EOF'
Initialize/reset the eixnpos database.

Usage:
  scripts/setup-db.sh [--fresh] [--seed-business] [-h|--help]

Options:
  --fresh          Remove backend/db.sqlite3 before migrating (fresh start).
  --seed-business  Seed default BusinessInfo values after migrating
                   (runs: manage.py seed_business_admin).
  -h, --help       Show this help.
EOF
}

FRESH=0
SEED_BUSINESS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fresh) FRESH=1 ;;
    --seed-business) SEED_BUSINESS=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "setup-db.sh: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

if (( FRESH )); then
  if [[ -f "$DB" ]]; then
    echo "==> removing $DB"
    rm -f "$DB"
  else
    echo "==> no existing database at $DB (fresh)"
  fi
fi

echo "==> applying migrations"
"${MANAGE[@]}" migrate

if (( SEED_BUSINESS )); then
  echo "==> seeding business info"
  "${MANAGE[@]}" seed_business_admin
fi

echo "==> database ready: $DB"
