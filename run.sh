#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -x .runtime/tools/scrcpy-linux-x86_64-v5.0.1/scrcpy ]]; then
  export PATH="$PWD/.runtime/tools/scrcpy-linux-x86_64-v5.0.1:$PATH"
fi
if [[ ! -x backend/.venv/bin/python || ! -d frontend/node_modules ]]; then
  echo 'Install dependencies first; see README.md.' >&2
  exit 1
fi
for port in 8000 5173; do
  if ss -H -ltn "sport = :$port" | rg -q .; then
    echo "Port $port is already in use. Stop the existing server first." >&2
    exit 1
  fi
done
mkdir -p .runtime
backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log --timeout-graceful-shutdown 3 > .runtime/backend.log 2>&1 &
backend_pid=$!
(cd frontend && exec node node_modules/vite/bin/vite.js) > .runtime/frontend.log 2>&1 &
frontend_pid=$!
cleanup() {
  kill "$frontend_pid" "$backend_pid" 2>/dev/null || true
  wait "$frontend_pid" "$backend_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM
printf 'Video Amplification: http://127.0.0.1:5173\nLogs: .runtime/backend.log and .runtime/frontend.log\nPress Ctrl+C to stop.\n'
wait -n "$backend_pid" "$frontend_pid"
