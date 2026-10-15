#!/usr/bin/env bash

set -Eeuo pipefail

TAG="${1:-}"

fail() {
    echo "build_release.sh: $*" >&2
    exit 1
}

command -v git >/dev/null || fail "git is required"
command -v tar >/dev/null || fail "tar is required"
command -v gzip >/dev/null || fail "gzip is required"
command -v sha256sum >/dev/null || fail "sha256sum is required"
command -v python3 >/dev/null || fail "python3 is required"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" \
    || fail "Run this from inside the G13 Zombie Control Center repository."

cd "$ROOT"

if [[ -z "$TAG" ]]; then
    [[ -f VERSION ]] \
        || fail "Required file missing from working tree: VERSION"

    DEFAULT_VERSION="$(tr -d '\r\n' < VERSION)"

    [[ "$DEFAULT_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        || fail "Invalid working-tree VERSION: $DEFAULT_VERSION"

    TAG="v$DEFAULT_VERSION"
fi

git rev-parse --verify "${TAG}^{commit}" >/dev/null 2>&1 \
    || fail "Git tag not found: $TAG"

VERSION="$(git show "${TAG}:VERSION" | tr -d '\r\n')"

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || fail "Invalid VERSION in $TAG: $VERSION"

[[ "$TAG" == "v$VERSION" ]] \
    || fail "Tag $TAG does not match VERSION $VERSION"

NAME="g13-zombie-control-center-$VERSION"
DIST="$ROOT/dist"
ARCHIVE="$DIST/$NAME.tar.gz"
CHECKSUM="$ARCHIVE.sha256"
CONTENTS="$DIST/$NAME.contents.txt"

SUPPORT_FILES=(
    VERSION
    LICENSE
    app-files.txt
    container-packages.txt
    requirements-runtime.txt
    install.sh
    zombiecc
    install_desktop.py
    install_device_permissions.py
    install_driver_service.py
    update_app.py
    uninstall.py
    README.md
    docs/INSTALL.md
    docs/RUNTIME.md
)

declare -a FILES
declare -A SEEN

add_file() {
    local file="$1"

    git cat-file -e "${TAG}:${file}" 2>/dev/null \
        || fail "Required file missing from $TAG: $file"

    if [[ ! -v "SEEN[$file]" ]]; then
        SEEN["$file"]=1
        FILES+=("$file")
    fi
}

for file in "${SUPPORT_FILES[@]}"; do
    add_file "$file"
done

while IFS= read -r file || [[ -n "$file" ]]; do
    file="${file%$'\r'}"

    [[ -n "$file" ]] || continue
    [[ "$file" == \#* ]] && continue

    add_file "$file"
done < <(git show "${TAG}:app-files.txt")

mkdir -p "$DIST"

rm -f "$ARCHIVE" "$CHECKSUM"

echo
echo "Building G13 Zombie Control Center $VERSION..."
echo "Source tag: $TAG"
echo

git archive \
    --format=tar \
    --prefix="$NAME/" \
    "$TAG" \
    -- "${FILES[@]}" \
    | gzip -9 > "$ARCHIVE"

tar -tzf "$ARCHIVE" > "$CONTENTS" \
    || fail "Archive verification failed"

for forbidden in \
    "/.git/" \
    "/.venv/" \
    "/__pycache__/" \
    "/profiles/" \
    "/tools/" \
    "/Old Files/" \
    "/g13-editor.sh" \
    "/g13-driver.service"
do
    if grep -Fq -- "$forbidden" "$CONTENTS"; then
        fail "Forbidden development path entered the release archive: $forbidden"
    fi
done

if grep -Eq "/test_[^/]*\.py$" "$CONTENTS"; then
    fail "Test files entered the release archive"
fi

VERIFY_DIR="$(mktemp -d "$DIST/.verify-$NAME.XXXXXX")"

cleanup_verify() {
    rm -rf -- "$VERIFY_DIR"
}

trap cleanup_verify EXIT

tar -xzf "$ARCHIVE" -C "$VERIFY_DIR"

RELEASE_DIR="$VERIFY_DIR/$NAME"

[[ -d "$RELEASE_DIR" ]] \
    || fail "Archive verification directory is missing"

bash -n "$RELEASE_DIR/install.sh"
bash -n "$RELEASE_DIR/zombiecc"

python3 -m py_compile \
    "$RELEASE_DIR/install_desktop.py" \
    "$RELEASE_DIR/install_device_permissions.py" \
    "$RELEASE_DIR/install_driver_service.py" \
    "$RELEASE_DIR/update_app.py" \
    "$RELEASE_DIR/uninstall.py"

while IFS= read -r path || [[ -n "$path" ]]; do
    path="${path%$'\r'}"

    [[ -n "$path" ]] || continue
    [[ "$path" == \#* ]] && continue

    [[ -f "$RELEASE_DIR/$path" ]] \
        || fail "Archive is missing application-manifest file: $path"
done < "$RELEASE_DIR/app-files.txt"

(
    cd "$DIST"
    sha256sum "$(basename "$ARCHIVE")" \
        > "$(basename "$CHECKSUM")"
)

read -r _ checksum_filename < "$CHECKSUM"

[[ "$checksum_filename" == "$(basename "$ARCHIVE")" ]] \
    || fail "Checksum file contains a non-portable archive path."

(
    cd "$DIST"
    sha256sum -c "$(basename "$CHECKSUM")" >/dev/null
) || fail "Release checksum verification failed."

echo
echo "Release created:"
echo
echo "  $ARCHIVE"
echo "  $CHECKSUM"
echo "  $CONTENTS"
echo
echo "SHA-256:"
cat "$CHECKSUM"
echo
