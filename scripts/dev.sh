#!/usr/bin/env bash
#
# dev.sh - start the eixnpos development servers, each in its own terminal.
#
#   backend : uv run backend/manage.py runserver   -> http://127.0.0.1:8000
#   frontend: npm run dev (in ./frontend)          -> http://127.0.0.1:5173
#
# Both servers are launched in separate terminal windows, so each keeps its
# own logs and can be restarted independently. This script returns as soon as
# they are started.
#
# Each server runs under a small supervisor inside its terminal: the server is
# put in its own process group, and when the terminal closes (SIGHUP) or you
# press Ctrl+C the whole group is sent SIGTERM and then SIGKILL. That stops
# Django's autoreloader and Vite/esbuild being orphaned and frees the port,
# even if a server ignores SIGHUP.
#
# If no terminal emulator can be found, both servers run in the background of
# this script instead, which then waits until Ctrl+C.
#
set -euo pipefail

SELF="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/${BASH_SOURCE[0]##*/}"
ROOT="$(cd -- "$(dirname -- "$SELF")/.." && pwd)"

usage() {
  cat <<'EOF'
Start the eixnpos development servers, each in its own terminal.

  backend : uv run backend/manage.py runserver   -> http://127.0.0.1:8000
  frontend: npm run dev (in ./frontend)          -> http://127.0.0.1:5173

Usage:
  scripts/dev.sh                 start both
  scripts/dev.sh --backend-only  start only Django
  scripts/dev.sh --frontend-only start only Vite
EOF
}

# --- supervise one server (runs inside its own terminal) -------------------

# run_supervised <label> <command...>
# Runs the command in its own process group and makes sure the entire group is
# torn down when this shell gets a signal or exits.
run_supervised() {
  local label="$1"; shift

  # Own process group + session, so the whole tree (reloader, node, esbuild)
  # can be signalled as one unit and is not killed behind our back by the
  # terminal's SIGHUP before we get to clean up.
  if command -v setsid >/dev/null 2>&1; then
    setsid "$@" &
  else
    set -m
    "$@" &
    set +m
  fi
  local pid=$!

  cleanup() {
    trap - HUP INT TERM EXIT
    [[ -n "$pid" ]] || return 0
    kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    local i=0
    while [ "$i" -lt 25 ] && kill -0 -- "-$pid" 2>/dev/null; do
      sleep 0.2
      i=$((i + 1))
    done
    kill -KILL -- "-$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  }
  trap 'cleanup; exit 143' HUP INT TERM
  trap cleanup EXIT

  local status=0
  wait "$pid" || status=$?
  trap - HUP INT TERM EXIT

  if (( status != 0 )); then
    echo
    echo "$label exited with status $status"
    read -rp "Press Enter to close..." _ || true
  fi
}

# serve <backend|frontend> - entry point used inside each terminal.
serve() {
  local which="${1:-}" dir cmd
  case "$which" in
    backend)  dir="$ROOT";          cmd=(uv run backend/manage.py runserver) ;;
    frontend) dir="$ROOT/frontend"; cmd=(npm run dev) ;;
    *) echo "dev.sh: unknown server '$which'" >&2; return 2 ;;
  esac
  cd "$dir" || return 1
  run_supervised "$which" "${cmd[@]}"
}

# --- terminal launchers ----------------------------------------------------

detect_launcher() {
  local c
  for c in omarchy-launch-terminal xdg-terminal-exec foot kitty ghostty \
           alacritty gnome-terminal konsole xterm; do
    command -v "$c" >/dev/null 2>&1 && { printf '%s' "$c"; return 0; }
  done
  return 1
}

# Detach a GUI launcher so it survives this script exiting and does not block
# the next launch (xdg-terminal-exec/foot run in the foreground otherwise).
detach() {
  if command -v setsid >/dev/null 2>&1; then
    setsid "$@" &
  else
    "$@" &
  fi
}

# new_terminal <launcher> <backend|frontend>
new_terminal() {
  local launcher="$1" label="$2"
  local inner="exec bash $(printf '%q' "$SELF") --_serve $(printf '%q' "$label")"
  case "$launcher" in
    omarchy-launch-terminal) detach omarchy-launch-terminal bash -lc "$inner" ;;
    xdg-terminal-exec)       detach xdg-terminal-exec --title="$label" bash -lc "$inner" ;;
    foot)                    detach foot -e bash -lc "$inner" ;;
    kitty)                   detach kitty bash -lc "$inner" ;;
    ghostty)                 detach ghostty -e bash -lc "$inner" ;;
    alacritty)               detach alacritty -e bash -lc "$inner" ;;
    gnome-terminal)          detach gnome-terminal -- bash -lc "$inner" ;;
    konsole)                 detach konsole -e bash -lc "$inner" ;;
    xterm)                   detach xterm -e bash -lc "$inner" ;;
  esac
}

# --- entry point -----------------------------------------------------------

main() {
  case "${1:-}" in
    --_serve) serve "${2:-}"; return $? ;;
    -h|--help) usage; return 0 ;;
  esac

  local run_backend=1 run_frontend=1
  case "${1:-}" in
    "") ;;
    --backend-only)  run_frontend=0 ;;
    --frontend-only) run_backend=0 ;;
    *) echo "dev.sh: unknown option: $1" >&2; usage >&2; return 2 ;;
  esac

  if (( run_frontend )) && [[ ! -d "$ROOT/frontend/node_modules" ]]; then
    echo "==> frontend: node_modules missing, running 'npm install' first"
    ( cd "$ROOT/frontend" && npm install )
  fi

  if launcher=$(detect_launcher); then
    (( run_backend ))  && new_terminal "$launcher" backend
    (( run_frontend )) && new_terminal "$launcher" frontend
    echo "==> started servers in separate terminals (launcher: $launcher)"
    return 0
  fi

  # --- fallback: no terminal emulator, run both in the background ----------
  echo "!!  No terminal emulator found; starting servers in the background." >&2
  echo "!!  Logs: $ROOT/.dev-backend.log  $ROOT/.dev-frontend.log" >&2

  local backend_pid="" frontend_pid=""
  cleanup() {
    local p
    for p in ${backend_pid:-} ${frontend_pid:-}; do
      kill -TERM "$p" 2>/dev/null || true   # each --_serve shell tears down its own group
    done
  }
  trap cleanup EXIT INT TERM

  if (( run_backend )); then
    bash "$SELF" --_serve backend  >"$ROOT/.dev-backend.log"  2>&1 &
    backend_pid=$!
  fi
  if (( run_frontend )); then
    bash "$SELF" --_serve frontend >"$ROOT/.dev-frontend.log" 2>&1 &
    frontend_pid=$!
  fi

  echo "==> running in the background; press Ctrl+C to stop."
  wait
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
