#!/bin/sh
set -eu

VERSION=${NOOKWIRE_SSH_VERSION:-1.0.2}
BASE_URL=${NOOKWIRE_SSH_BASE_URL:-https://raw.githubusercontent.com/lars-hagen/nookwire-ssh/v$VERSION}
PREFIX=${NOOKWIRE_SSH_PREFIX:-"$HOME/.local"}
BIN_DIR="$PREFIX/bin"
TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/nookwire-ssh-install.XXXXXX")
HAD_LAUNCHER=0
HAD_SERVER=0
INSTALL_STARTED=0
COMMITTED=0

cleanup() {
  status=$?
  trap - 0 HUP INT TERM
  if [ "$INSTALL_STARTED" -eq 1 ] && [ "$COMMITTED" -ne 1 ]; then
    if [ "$HAD_LAUNCHER" -eq 1 ]; then
      mv "$TEMP_DIR/backup/nookwire-ssh" "$BIN_DIR/nookwire-ssh"
    else
      rm -f "$BIN_DIR/nookwire-ssh"
    fi
    if [ "$HAD_SERVER" -eq 1 ]; then
      mv "$TEMP_DIR/backup/nookwire_ssh.py" "$BIN_DIR/nookwire_ssh.py"
    else
      rm -f "$BIN_DIR/nookwire_ssh.py"
    fi
  fi
  rm -rf "$TEMP_DIR"
  exit "$status"
}
trap cleanup 0 HUP INT TERM

command -v curl >/dev/null 2>&1 || {
  printf '%s\n' "nookwire-ssh: curl is required" >&2
  exit 1
}

curl -fsSL "$BASE_URL/nookwire-ssh" -o "$TEMP_DIR/nookwire-ssh"
curl -fsSL "$BASE_URL/nookwire_ssh.py" -o "$TEMP_DIR/nookwire_ssh.py"

mkdir -p "$BIN_DIR"
chmod 755 "$TEMP_DIR/nookwire-ssh"
chmod 644 "$TEMP_DIR/nookwire_ssh.py"
mkdir "$TEMP_DIR/backup"
for destination in "$BIN_DIR/nookwire-ssh" "$BIN_DIR/nookwire_ssh.py"; do
  if [ -L "$destination" ] || { [ -e "$destination" ] && [ ! -f "$destination" ]; }; then
    printf 'nookwire-ssh: refusing unsafe install destination: %s\n' "$destination" >&2
    exit 1
  fi
done
if [ -f "$BIN_DIR/nookwire-ssh" ]; then
  cp -p "$BIN_DIR/nookwire-ssh" "$TEMP_DIR/backup/nookwire-ssh"
  HAD_LAUNCHER=1
fi
if [ -f "$BIN_DIR/nookwire_ssh.py" ]; then
  cp -p "$BIN_DIR/nookwire_ssh.py" "$TEMP_DIR/backup/nookwire_ssh.py"
  HAD_SERVER=1
fi
INSTALL_STARTED=1
mv "$TEMP_DIR/nookwire-ssh" "$BIN_DIR/nookwire-ssh"
mv "$TEMP_DIR/nookwire_ssh.py" "$BIN_DIR/nookwire_ssh.py"
COMMITTED=1

printf 'Installed nookwire-ssh to %s\n' "$BIN_DIR/nookwire-ssh"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) printf 'Add %s to PATH: export PATH="%s:$PATH"\n' "$BIN_DIR" "$BIN_DIR" ;;
esac
