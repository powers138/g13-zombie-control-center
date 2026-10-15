#!/usr/bin/env bash
# Host-side editor launcher. The independent driver service is not managed here.
set -euo pipefail

PROJECT_DIR="$(
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
    pwd
)"

# Keep the latest launch output, including errors, available after leaving Steam.
LOG_FILE="$HOME/g13-editor-launch.log"
exec > "$LOG_FILE" 2>&1

printf 'G13 Zombie Control Center editor launch: %s\n' "$(date -Is)"
printf 'Project: %s\n' "$PROJECT_DIR"
printf 'Desktop: %s\n' "${XDG_CURRENT_DESKTOP:-not set}"
printf 'DISPLAY: %s\n' "${DISPLAY:-not set}"
printf 'WAYLAND_DISPLAY: %s\n' "${WAYLAND_DISPLAY:-not set}"

if [[ -n "${CONTAINER_ID:-}" ]]; then
    echo "Launch this script from the host, not from inside Distrobox."
    exit 1
fi

if [[ ! -f "$PROJECT_DIR/editor.py" ]]; then
    echo "Editor not found. Keep g13-editor.sh beside editor.py."
    exit 1
fi

if [[ ! -x /usr/bin/distrobox ]]; then
    echo "Distrobox was not found at /usr/bin/distrobox."
    exit 1
fi

cd -- "$PROJECT_DIR"

# Stay attached to the editor command; do not use nohup, disown, or '&'.
# Pass the project directory as an argument rather than embedding it in shell code.
exec /usr/bin/distrobox enter --name g13-fedora --no-tty -- bash -lc '
    cd -- "$1" || exit 1
    exec ./.venv/bin/python -u editor.py
' g13-editor "$PROJECT_DIR"
