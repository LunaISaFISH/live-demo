#!/bin/bash
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
for candidate in "$PWD/.venv/bin/python" python3.13 python3.12 python3.11 python3; do
    if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        runtime="$candidate"
        break
    fi
done
if [ -z "${runtime:-}" ]; then
    echo 'Python 3.11+ is required. Install with: brew install python@3.13'
    read -r -p 'Press Enter to close.'
    exit 1
fi
"$runtime" run.py --check
port=$("$PWD/.venv/bin/python" -c 'import sys; sys.path.insert(0,"prototype"); import server; print(server.PORT)')
url="http://localhost:$port"
if curl --silent --fail "$url/api/participants" >/dev/null; then
    open "$url"
    echo "FitCoach is already running at $url"
    exit 0
fi
"$PWD/.venv/bin/python" -u prototype/server.py &
server_pid=$!
trap 'kill -TERM "$server_pid" 2>/dev/null || true' INT TERM EXIT
for ((i=0; i<60; i++)); do
    if curl --silent --fail "$url/api/participants" >/dev/null; then
        open "$url"
        echo 'Ready. Keep this Terminal window open during your presentation.'
        echo 'Press Control+C when finished.'
        break
    fi
    if ! kill -0 "$server_pid" 2>/dev/null; then
        wait "$server_pid"
        exit 1
    fi
    sleep 1
done
if [ "$i" -eq 60 ]; then
    echo 'Server did not become ready within 60 seconds. See the error above.'
    exit 1
fi
wait "$server_pid"
