#!/usr/bin/env bash
# G13 Zombie Control Center -- installer stage 1.
# App payload + Fedora runtime + launcher only. No host packages, services,
# desktop entries, permissions, or live profile/settings changes.
# Re-running the SAME payload is supported. Upgrades/downgrades are refused
# until the separate update/rollback path has been implemented.
set -Eeuo pipefail
shopt -s inherit_errexit

CONTAINER_NAME=zombiecc
CONTAINER_IMAGE=quay.io/fedora/fedora:44
OWNER_LABEL=io.zombiecc.installer
OWNER_VALUE=stage1-v1
MARKER_NAME=.zombiecc-stage1
DRY_RUN=0
ASSUME_YES=0
LOG_FILE=
STAGE=
LAUNCH_TMP=

UINPUT_PATH=/dev/uinput
INPUT_DIR=/dev/input
HOST_INPUT_READABLE=0
HOST_INPUT_TOTAL=0
SELINUX_DEVICE_STATUS="not applicable"

fail() {
    printf 'install.sh: %s\n' "$*" >&2
    if [[ -n "$LOG_FILE" ]]; then
        printf 'ERROR: %s\n' "$*" >> "$LOG_FILE"
    fi
    exit 1
}

cleanup() {
    local status=$?
    trap - EXIT
    [[ -z "$LAUNCH_TMP" ]] || rm -f -- "$LAUNCH_TMP"
    [[ -z "$STAGE" ]] || rm -rf -- "$STAGE"
    if (( status != 0 )) && [[ -n "$LOG_FILE" ]]; then
        printf '\nStopped before completion. Log: %s\n' "$LOG_FILE" >&2
        printf 'Partial app/container setup is retained for diagnosis and retry.\n' >&2
        printf 'No existing container or live settings/profiles were removed.\n' >&2
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

usage() {
    cat <<'HELP'
Usage: ./install.sh [--dry-run] [--yes]

Run on the Linux host as your normal user, from a source/release folder
containing app-files.txt, container-packages.txt, requirements-runtime.txt,
and zombiecc. No Git checkout or activated venv is required.

  --dry-run   Validate inputs and inspect Podman; show the plan only.
              Does not create/start containers or write installation files.
  --yes       Skip the install confirmation (not the safety checks).
  --help      Show this help.

Destinations (respect XDG_DATA_HOME and XDG_STATE_HOME):
  ~/.local/share/zombiecc/app/          manifest payload
  ~/.local/share/zombiecc/app/.venv/   container-created Python runtime
  ~/.local/bin/zombiecc               host launcher
  ~/.local/state/zombiecc/logs/       timestamped installer log

This stage does NOT install/enable a driver service, modify device/group
permissions, create desktop entries, or launch the application/driver.
It does not change any development or disposable test container.
Existing unrelated destinations are refused. An installation with different
source contents is refused, not treated as an automatic upgrade.
HELP
}

for argument in "$@"; do
    case "$argument" in
        --dry-run) DRY_RUN=1 ;;
        --yes) ASSUME_YES=1 ;;
        --help|-h) usage; exit 0 ;;
        *) fail "Unknown argument: $argument (use --help)." ;;
    esac
done

# Host-only guard. Do not add an override to bypass this in production.
if [[ -n "${CONTAINER_ID:-}" || -e /run/.containerenv || -e /.dockerenv ]]; then
    fail 'Run on the host, not inside Distrobox. Use exit first.'
fi
(( EUID != 0 )) || fail 'Run as your normal user, without sudo.'
[[ "$(uname -s)" == Linux ]] || fail 'This installer is for Linux.'
[[ "$(uname -m)" == x86_64 ]] || fail 'Only the tested x86_64 runtime is supported in this stage.'
[[ "${HOME:-}" == /* ]] || fail 'HOME must be an absolute path.'

for tool in distrobox podman crun python3 flock realpath sha256sum cmp cp mv mkdir \
    mktemp install ln rm chmod bash date cat dirname uname; do
    command -v "$tool" > /dev/null || fail "Required host command not found: $tool"
done
DISTROBOX_BIN="$(command -v distrobox)"
PODMAN_BIN="$(command -v podman)"

DISTROBOX_VERSION="$("$DISTROBOX_BIN" --version 2>/dev/null)" \
    || fail 'Could not determine the installed Distrobox version.'
if [[ "$DISTROBOX_VERSION" =~ ([0-9]+)\.([0-9]+)\.([0-9]+)(\.([0-9]+))? ]]; then
    distrobox_major="${BASH_REMATCH[1]}"
    distrobox_minor="${BASH_REMATCH[2]}"
    distrobox_patch="${BASH_REMATCH[3]}"
    distrobox_extra="${BASH_REMATCH[5]:-0}"
else
    fail "Unrecognized Distrobox version: $DISTROBOX_VERSION"
fi
if (( distrobox_major < 1 \
    || (distrobox_major == 1 && distrobox_minor < 8) \
    || (distrobox_major == 1 && distrobox_minor == 8 && distrobox_patch < 1) \
    || (distrobox_major == 1 && distrobox_minor == 8 \
        && distrobox_patch == 1 && distrobox_extra < 2) )); then
    fail "Distrobox 1.8.1.2 or newer is required; found $DISTROBOX_VERSION"
fi

# Avoid a user setting silently selecting Docker or a different named container.
export DBX_CONTAINER_MANAGER=podman

xdg_home() {
    if [[ "$1" == /* ]]; then printf '%s\n' "$1"; else printf '%s\n' "$2"; fi
}
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export XDG_DATA_HOME="$(xdg_home "${XDG_DATA_HOME:-}" "$HOME/.local/share")"
export XDG_CONFIG_HOME="$(xdg_home "${XDG_CONFIG_HOME:-}" "$HOME/.config")"
export XDG_STATE_HOME="$(xdg_home "${XDG_STATE_HOME:-}" "$HOME/.local/state")"
BASE_DIR="$XDG_DATA_HOME/zombiecc"
APP_DIR="$BASE_DIR/app"
LOG_DIR="$XDG_STATE_HOME/zombiecc/logs"
LAUNCHER_DIR="$HOME/.local/bin"
LAUNCHER="$LAUNCHER_DIR/zombiecc"

# Compare canonical paths without changing the paths the launcher will use.
source_real="$(realpath -m -- "$SOURCE_DIR")"
app_real="$(realpath -m -- "$APP_DIR")"
if [[ "$source_real" == "$app_real" || "$source_real" == "$app_real/"* \
    || "$app_real" == "$source_real/"* ]]; then
    fail 'Source and installed app directories must be separate, not nested.'
fi

# Only ordinary files with explicit safe relative paths may enter the payload.
# No globs, options, dot directories, parent traversal, or source symlinks.
check_source_file() {
    local relative="$1" part current="$SOURCE_DIR"
    local -a parts
    [[ "$relative" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$ ]] \
        || fail "Invalid manifest path: $relative"
    IFS=/ read -r -a parts <<< "$relative"
    for part in "${parts[@]}"; do
        current="$current/$part"
        [[ ! -L "$current" ]] || fail "Source symlink is not allowed: $relative"
    done
    [[ -f "$current" && -r "$current" ]] || fail "Missing/unreadable source file: $relative"
}
for input in app-files.txt container-packages.txt requirements-runtime.txt zombiecc; do
    check_source_file "$input"
done
bash -n "$SOURCE_DIR/zombiecc" || fail 'The zombiecc launcher has a shell syntax error.'

declare -a APP_FILES=() PACKAGES=() REQUIREMENTS=()
declare -A SEEN=()
while IFS= read -r path || [[ -n "$path" ]]; do
    path="${path%$'\r'}"
    [[ -n "$path" && "$path" != \#* ]] || continue
    check_source_file "$path"
    case "$path" in
        test_*|*/test_*|__pycache__/*|*/__pycache__/*|profiles/*|config/*|tools/*|docs/*|zombiecc|install.sh|g13-editor.sh|*.service)
            fail "Non-app file in application manifest: $path" ;;
    esac
    [[ ! -v "SEEN[$path]" ]] || fail "Duplicate app manifest entry: $path"
    SEEN["$path"]=1
    APP_FILES+=("$path")
done < "$SOURCE_DIR/app-files.txt"
(( ${#APP_FILES[@]} > 0 )) || fail 'app-files.txt is empty.'
for required in VERSION app_version.py editor.py g13-test.py driver_service.py \
    profile_manager.py app_paths.py user_data_bootstrap.py \
    resources/defaults/default-profile.json resources/defaults/settings.json \
    app-files.txt container-packages.txt requirements-runtime.txt; do
    [[ -v "SEEN[$required]" ]] || fail "Required app manifest entry missing: $required"
done

APP_VERSION="$(cat "$SOURCE_DIR/VERSION")"

[[ "$APP_VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] \
    || fail 'VERSION must use MAJOR.MINOR.PATCH format.'

SEEN=()
while IFS= read -r package || [[ -n "$package" ]]; do
    package="${package%$'\r'}"
    [[ -n "$package" && "$package" != \#* ]] || continue
    [[ "$package" =~ ^[A-Za-z0-9][A-Za-z0-9+_.-]*$ ]] || fail "Invalid RPM package name: $package"
    [[ ! -v "SEEN[$package]" ]] || fail "Duplicate RPM package: $package"
    SEEN["$package"]=1
    PACKAGES+=("$package")
done < "$SOURCE_DIR/container-packages.txt"
(( ${#PACKAGES[@]} > 0 )) || fail 'container-packages.txt is empty.'

SEEN=()
while IFS= read -r requirement || [[ -n "$requirement" ]]; do
    requirement="${requirement%$'\r'}"
    [[ -n "$requirement" && "$requirement" != \#* ]] || continue
    [[ "$requirement" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9][A-Za-z0-9_.+-]*$ ]] \
        || fail "Expected a pinned name==version, not shell code/options: $requirement"
    name="${requirement%%==*}"; name="${name,,}"; name="${name//_/-}"
    [[ "$name" != evdev && "$name" != pygobject ]] || fail "$name must come from Fedora, not pip."
    [[ ! -v "SEEN[$name]" ]] || fail "Duplicate Python dependency: $name"
    SEEN["$name"]=1
    REQUIREMENTS+=("$requirement")
done < "$SOURCE_DIR/requirements-runtime.txt"
for name in pyside6 hid lupa; do
    [[ -v "SEEN[$name]" ]] || fail "Required Python dependency missing: $name"
done

check_host_device_access() {
    local path selinux_result
    local -a event_devices=()

    [[ -e "$UINPUT_PATH" ]] \
        || fail "Linux virtual input device is missing: $UINPUT_PATH"

    [[ -w "$UINPUT_PATH" ]] || fail \
        "Zombie cannot write $UINPUT_PATH. Run 'python3 install_device_permissions.py --dry-run' and then 'python3 install_device_permissions.py' from this release directory. If group membership changes, fully log out or reboot before retrying."

    if [[ -d "$INPUT_DIR" ]]; then
        shopt -s nullglob
        event_devices=("$INPUT_DIR"/event*)
        shopt -u nullglob
    fi

    HOST_INPUT_TOTAL="${#event_devices[@]}"
    HOST_INPUT_READABLE=0

    for path in "${event_devices[@]}"; do
        if [[ -r "$path" ]]; then
            (( HOST_INPUT_READABLE += 1 ))
        fi
    done

    if command -v getsebool > /dev/null 2>&1; then
        if selinux_result="$(
            getsebool container_use_devices 2>/dev/null
        )"; then
            case "$selinux_result" in
                *' --> on')
                    SELINUX_DEVICE_STATUS=\
"container_use_devices=on"
                    ;;

                *' --> off')
                    fail \
                        "SELinux container_use_devices is off. On Bazzite/Fedora, enable it with 'sudo setsebool -P container_use_devices on', then retry."
                    ;;

                *)
                    SELINUX_DEVICE_STATUS=\
"container_use_devices state unknown"
                    ;;
            esac
        fi
    fi
}

check_host_device_access


fingerprint() {
    local root="$1" launcher="$2" path
    {
        printf '%s\0' "$CONTAINER_IMAGE" "$OWNER_VALUE"
        for path in "${APP_FILES[@]}"; do
            printf '%s\0' "$path"
            sha256sum < "$root/$path"
        done
        printf 'launcher\0'
        sha256sum < "$launcher"
    } | sha256sum | { read -r digest rest; printf '%s\n' "$digest"; }
}
FINGERPRINT="$(fingerprint "$SOURCE_DIR" "$SOURCE_DIR/zombiecc")"

check_targets() {
    local path current relative
    for path in "$BASE_DIR" "$APP_DIR" "$LOG_DIR" "$LAUNCHER_DIR"; do
        [[ ! -L "$path" ]] || fail "Refusing a symlinked installation directory: $path"
        if [[ -e "$path" ]]; then
            [[ -d "$path" && -O "$path" && -w "$path" ]] || fail "Directory is not owned/writable by you: $path"
        fi
    done
    if [[ -e "$APP_DIR" ]]; then
        path="$APP_DIR/$MARKER_NAME"
        [[ ! -L "$path" && -f "$path" && -O "$path" ]] || fail "Existing app directory is not owned by this installer: $APP_DIR"
        [[ "$(cat "$path")" == "$FINGERPRINT" ]] || fail 'Installed payload differs. This stage does not perform upgrades or downgrades.'
        for relative in "${APP_FILES[@]}"; do
            current="$APP_DIR/$relative"
            path="$current"
            while [[ "$path" != "$APP_DIR" ]]; do
                [[ ! -L "$path" ]] || fail "Symlink in installed payload: $relative"
                path="${path%/*}"
            done
            [[ -f "$current" ]] && cmp -s -- "$SOURCE_DIR/$relative" "$current" \
                || fail "Installed file was changed or removed; leaving it alone: $relative"
        done
        [[ ! -L "$APP_DIR/.venv" ]] || fail 'Installed .venv is a symlink; refusing to modify it.'
    fi
    [[ ! -L "$LAUNCHER" ]] || fail "Existing launcher is a symlink; leaving it alone: $LAUNCHER"
    if [[ -e "$LAUNCHER" ]]; then
        [[ -f "$LAUNCHER" && -O "$LAUNCHER" ]] && cmp -s -- "$SOURCE_DIR/zombiecc" "$LAUNCHER" \
            || fail "A different launcher already exists; leaving it alone: $LAUNCHER"
    fi
}
check_targets

podman_mode="$("$PODMAN_BIN" info --format '{{.Host.Security.Rootless}} {{.Host.ServiceIsRemote}}')" \
    || fail 'Cannot query Podman. No installation changes have been made.'
[[ "$podman_mode" == 'true false' ]] || fail 'A local rootless Podman engine is required.'
CONTAINER_EXISTS=0
check_container() {
    local status field value running processes paused
    CONTAINER_EXISTS=0
    if "$PODMAN_BIN" container exists "$CONTAINER_NAME"; then
        CONTAINER_EXISTS=1
    else
        status=$?
        (( status == 1 )) && return 0
        fail "Podman container check failed (exit $status); this is not treated as a missing container."
    fi
    local -a queries=(
        '{{index .Config.Labels "manager"}}'
        '{{index .Config.Labels "io.zombiecc.installer"}}'
        '{{.Config.Image}}'
        '{{.OCIRuntime}}'
        '{{index .Config.Labels "distrobox.unshare_groups"}}'
        '{{index .Config.Annotations "run.oci.keep_original_groups"}}'
    )
    local -a expected=(distrobox "$OWNER_VALUE" "$CONTAINER_IMAGE" crun 0 1)
    for field in "${!queries[@]}"; do
        value="$("$PODMAN_BIN" container inspect --format "${queries[$field]}" "$CONTAINER_NAME")" \
            || fail "Cannot inspect container: $CONTAINER_NAME"
        [[ "$value" == "${expected[$field]}" ]] \
            || fail "Container '$CONTAINER_NAME' does not match this installer (check $((field+1)): '$value'). It will not be replaced."
    done
    paused="$("$PODMAN_BIN" container inspect --format '{{.State.Paused}}' "$CONTAINER_NAME")" \
        || fail 'Cannot read container pause state.'
    [[ "$paused" == false ]] || fail "Container '$CONTAINER_NAME' is paused; it will not be changed."
    running="$("$PODMAN_BIN" container inspect --format '{{.State.Running}}' "$CONTAINER_NAME")" \
        || fail 'Cannot read container state.'
    if [[ "$running" == true ]]; then
        processes="$("$PODMAN_BIN" top "$CONTAINER_NAME" args)" || fail 'Cannot check running container processes.'
        if [[ "$processes" == *editor.py* || "$processes" == *g13-test.py* || "$processes" == *"$APP_DIR/.venv/"* ]]; then
            fail "Zombie is running in '$CONTAINER_NAME'. Close it and stop its driver before rerunning this installer."
        fi
    fi
}
check_container

printf '\nG13 Zombie Control Center -- stage 1 installation plan\n'
printf 'Version:      %s\n' "$APP_VERSION"
printf 'Source:       %s\nApplication:  %s\nPython:       %s/.venv/bin/python\nLauncher:     %s\n' \
    "$SOURCE_DIR" "$APP_DIR" "$APP_DIR" "$LAUNCHER"
printf 'Container:    %s (%s)\n' "$CONTAINER_NAME" "$CONTAINER_IMAGE"
printf 'Payload:      %s files; %s Fedora packages; %s pinned Python dependencies\n' \
    "${#APP_FILES[@]}" "${#PACKAGES[@]}" "${#REQUIREMENTS[@]}"
if (( CONTAINER_EXISTS )); then
    printf 'Container:    reuse matching installer-owned container; never delete/rebuild it\n'
else
    printf 'Container:    create new rootless Distrobox using crun\n'
fi
if [[ -d "$APP_DIR" ]]; then printf 'App action:   retry/recheck identical payload\n'; else printf 'App action:   install manifest-only payload\n'; fi
printf 'Python pins:  %s\n' "${REQUIREMENTS[*]}"
printf 'Host devices: uinput writable; %s/%s input event devices readable\n' \
    "$HOST_INPUT_READABLE" "$HOST_INPUT_TOTAL"
printf 'SELinux:      %s\n' "$SELINUX_DEVICE_STATUS"

if (( HOST_INPUT_TOTAL == 0 )); then
    printf 'NOTE:          No input event devices were found; physical-state Lua functions will be unavailable.\n'

elif (( HOST_INPUT_READABLE < HOST_INPUT_TOTAL )); then
    printf 'NOTE:          Some input devices are unreadable; physical-state Lua functions may not see them.\n'
fi

printf 'Unchanged:    live profiles/settings, existing driver service, device permissions,\n'
printf '              desktop entries, and non-production containers.\n'
printf 'Checks:       host device access, container config, dependencies, Qt links,\n'
printf '              source syntax, factory JSON.\n'
printf 'Not tested:   G13 presence/access or GUI startup by this installer.\n\n'
if (( DRY_RUN )); then
    printf 'DRY RUN complete. No installation files written and no containers created/started.\n'
    exit 0
fi
if (( ! ASSUME_YES )); then
    [[ -t 0 ]] || fail 'Interactive confirmation required; use --yes for an unattended run.'
    read -r -p 'Proceed with this stage only? [y/N] ' reply
    [[ "$reply" == y || "$reply" == Y ]] || { printf 'Cancelled.\n'; exit 0; }
fi

umask 077
mkdir -p -- "$BASE_DIR" "$LOG_DIR" "$LAUNCHER_DIR"
[[ ! -L "$BASE_DIR/.install.lock" ]] || fail 'Installer lock path is a symlink.'
if [[ -e "$BASE_DIR/.install.lock" ]]; then
    [[ -f "$BASE_DIR/.install.lock" && -O "$BASE_DIR/.install.lock" ]] || fail 'Invalid installer lock file.'
fi
exec 9>> "$BASE_DIR/.install.lock"
flock -n 9 || fail 'Another installer is already running for this destination.'
LOG_FILE="$(mktemp "$LOG_DIR/install-$(date +%Y%m%d-%H%M%S)-XXXXXX.log")"
log() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$LOG_FILE"; }
run() { "$@" 9>&- >> "$LOG_FILE" 2>&1 || fail "Stage failed: $CURRENT_STEP. See $LOG_FILE"; }
log "Installer log: $LOG_FILE"
# Repeat safety checks after acquiring the lock, before changing the payload.
check_targets
check_container
[[ "$(fingerprint "$SOURCE_DIR" "$SOURCE_DIR/zombiecc")" == "$FINGERPRINT" ]] \
    || fail 'Source changed during preflight; rerun against a stable source folder.'

CURRENT_STEP='stage manifest payload'
log '[1/6] Staging the exact application manifest.'
STAGE="$(mktemp -d "$BASE_DIR/.install-stage.XXXXXX")"
mkdir -- "$STAGE/app"
for path in "${APP_FILES[@]}"; do
    mkdir -p -- "$(dirname -- "$STAGE/app/$path")"
    cp -- "$SOURCE_DIR/$path" "$STAGE/app/$path"
done
cp -- "$SOURCE_DIR/zombiecc" "$STAGE/zombiecc"
[[ "$(fingerprint "$STAGE/app" "$STAGE/zombiecc")" == "$FINGERPRINT" ]] \
    || fail 'Source changed while copying; refusing a mixed-version payload.'
printf '%s\n' "$FINGERPRINT" > "$STAGE/app/$MARKER_NAME"

CURRENT_STEP='create or validate production container'
log '[2/6] Preparing the production container (details in log).'
if (( ! CONTAINER_EXISTS )); then
    run "$DISTROBOX_BIN" create --name "$CONTAINER_NAME" --image "$CONTAINER_IMAGE" \
        --yes --no-entry --home "$HOME" \
        --additional-flags "--runtime=crun --label=$OWNER_LABEL=$OWNER_VALUE"
fi
check_container

CURRENT_STEP='install Fedora runtime packages'
log '[3/6] Initializing Distrobox and installing Fedora runtime packages.'
# No sudo/package commands execute on the host. Arguments are never eval-ed.
run "$DISTROBOX_BIN" enter --name "$CONTAINER_NAME" --no-tty -- /bin/bash -c '
    set -euo pipefail
    expected_uid="$1"; shift
    . /etc/os-release
    [[ "$ID" == fedora && "$VERSION_ID" == 44 ]] || { echo "Expected Fedora 44." >&2; exit 1; }
    [[ "$(id -u)" == "$expected_uid" && "$(id -u)" != 0 ]] || { echo "Container user mismatch." >&2; exit 1; }
    exec sudo -n dnf install -y -- "$@"
' zombiecc-package-setup "$EUID" "${PACKAGES[@]}"

CURRENT_STEP='install application payload'
log '[4/6] Installing application files without changing live user data.'
check_targets
if [[ ! -e "$APP_DIR" ]]; then
    mv -T -- "$STAGE/app" "$APP_DIR"
fi

CURRENT_STEP='create and validate container Python runtime'
log '[5/6] Building the venv at its final path and checking runtime libraries.'
run "$DISTROBOX_BIN" enter --name "$CONTAINER_NAME" --no-tty -- /bin/bash -c '
    set -euo pipefail
    app_dir="$1"; expected_marker="$2"
    [[ -f "$app_dir/.zombiecc-stage1" && "$(cat "$app_dir/.zombiecc-stage1")" == "$expected_marker" ]] \
        || { echo "Host application directory is not shared correctly." >&2; exit 1; }
    unset PYTHONHOME PYTHONPATH
    export PYTHONNOUSERSITE=1
    # Do not let per-user pip settings redirect writes outside the venv.
    for setting in ${!PIP_@}; do unset "$setting"; done
    export PIP_CONFIG_FILE=/dev/null
    export PIP_DISABLE_PIP_VERSION_CHECK=1
    cd /
    python3 -c "import sys; assert sys.version_info[:2] == (3, 14), sys.version"
    [[ ! -L "$app_dir/.venv" ]] || { echo "Refusing symlinked venv." >&2; exit 1; }
    if [[ -e "$app_dir/.venv" ]]; then
        [[ -f "$app_dir/.venv/pyvenv.cfg" && ! -L "$app_dir/.venv/pyvenv.cfg" ]] \
            || { echo "Unrecognized/partial venv; leaving it for inspection." >&2; exit 1; }
        "$app_dir/.venv/bin/python" -c "import sys; assert sys.prefix != sys.base_prefix; assert sys.version_info[:2] == (3,14)"
    fi
    python3 -m venv --system-site-packages "$app_dir/.venv"
    "$app_dir/.venv/bin/python" -m pip install --require-virtualenv --no-input \
        --only-binary=:all: --index-url https://pypi.org/simple \
        -r "$app_dir/requirements-runtime.txt"
' zombiecc-python-setup "$APP_DIR" "$FINGERPRINT"

# This check intentionally does NOT import editor.py, g13-test.py, keyboard.py,
# or instantiate ProfileManager. That would touch hardware/IPC or live data.
run "$DISTROBOX_BIN" enter --name "$CONTAINER_NAME" --no-tty -- /bin/bash -c '
    unset PYTHONHOME PYTHONPATH
    export PYTHONNOUSERSITE=1
    cd / || exit 1
    exec "$1/.venv/bin/python" - "$1"
' zombiecc-runtime-check "$APP_DIR" <<'PY'
import importlib
import importlib.metadata
import json
from pathlib import Path
import site
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
venv = root / '.venv'
if Path(sys.prefix).resolve() != venv or site.ENABLE_USER_SITE is not False:
    raise RuntimeError('Python isolation check failed.')
for name in ('PySide6', 'hid', 'lupa', 'evdev', 'gi'):
    module = importlib.import_module(name)
    origin = Path(module.__file__).resolve()
    if name in ('evdev', 'gi'):
        valid = origin.is_relative_to('/usr/lib') or origin.is_relative_to('/usr/lib64')
    else:
        valid = origin.is_relative_to(venv)
    if not valid:
        raise RuntimeError(f'Unexpected package origin: {name}: {origin}')
    print(f'OK: {name}: {origin}')
for line in (root / 'requirements-runtime.txt').read_text().splitlines():
    if line and not line.startswith('#'):
        name, expected = line.split('==', 1)
        actual = importlib.metadata.version(name)
        if actual != expected:
            raise RuntimeError(f'{name}: expected {expected}, got {actual}')
from PySide6 import QtCore, QtGui, QtWidgets
from gi.repository import Gio, GLib
from lupa.lua51 import LuaRuntime
if LuaRuntime().eval('_VERSION') != 'Lua 5.1':
    raise RuntimeError('Lua 5.1 runtime check failed.')
plugins = Path(QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.LibraryPath.PluginsPath)) / 'platforms'
wayland = sorted(plugins.glob('libqwayland*.so'))
if not wayland:
    raise RuntimeError('Wayland platform plugins are missing.')
for plugin in [plugins / 'libqxcb.so', *wayland]:
    result = subprocess.run(['ldd', str(plugin)], capture_output=True, text=True, timeout=20)
    output = result.stdout + result.stderr
    if result.returncode or 'not found' in output:
        raise RuntimeError(f'Qt platform dependency check failed: {plugin}\n{output}')
    print(f'OK: native dependencies: {plugin.name}')
for line in (root / 'app-files.txt').read_text().splitlines():
    if line.endswith('.py'):
        path = root / line
        compile(path.read_bytes(), str(path), 'exec')
for name in ('settings.json', 'default-profile.json'):
    value = json.loads((root / 'resources/defaults' / name).read_text())
    if not isinstance(value, dict):
        raise RuntimeError(f'Factory {name} must be a JSON object.')
print('OK: source syntax and factory JSON. No application/driver was started.')
PY

CURRENT_STEP='publish host launcher'
log '[6/6] Installing the host launcher after successful runtime checks.'
check_targets
if [[ ! -e "$LAUNCHER" ]]; then
    LAUNCH_TMP="$(mktemp "$LAUNCHER_DIR/.zombiecc-install.XXXXXX")"
    install -m 0755 -- "$STAGE/zombiecc" "$LAUNCH_TMP"
    # Hard-link publication is atomic and cannot overwrite a racing new file.
    ln -- "$LAUNCH_TMP" "$LAUNCHER" || fail 'Launcher path appeared during installation; it was not overwritten.'
    rm -f -- "$LAUNCH_TMP"
    LAUNCH_TMP=
else
    chmod 0755 -- "$LAUNCHER"
fi
log 'Stage 1 completed. No editor/driver was started; no service was changed.'
printf '\nInstalled launcher: %s\nLog: %s\n' "$LAUNCHER" "$LOG_FILE"
printf 'Driver service and desktop/menu integration are still pending.\n'
printf 'Only one G13 driver should control the device at a time.\n'
