#!/bin/sh
# Render + install the Domovoi satellite systemd units for the CURRENT user
# (replaces the PROVISIONING.md §8 heredoc). Installs:
#   domovoi-satellite.service       (always)
#   domovoi-provisioning.service    (--with-provisioning: USB adoption mode)
#   domovoi-kiosk.service           (--with-kiosk: video satellites)
#
# Usage:  sudo -E sh satellite/scripts/install-service.sh [--with-provisioning] [--with-kiosk]
# (run from the repo checkout; -E preserves $SUDO_USER's env resolution below)

set -eu

USER_NAME="${SUDO_USER:-$(id -un)}"
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

render() {
  # $1 = source unit file, $2 = unit name
  sed -e "s|@USER@|$USER_NAME|g" -e "s|@HOME@|$HOME_DIR|g" "$1" \
    >"/etc/systemd/system/$2"
  echo "installed /etc/systemd/system/$2"
}

render "$HERE/domovoi-satellite.service" domovoi-satellite.service
UNITS="domovoi-satellite"

for arg in "$@"; do
  case "$arg" in
    --with-provisioning)
      render "$HERE/scripts/domovoi-provisioning.service" domovoi-provisioning.service
      UNITS="$UNITS domovoi-provisioning"
      ;;
    --with-kiosk)
      render "$HERE/domovoi-kiosk.service" domovoi-kiosk.service
      UNITS="$UNITS domovoi-kiosk"
      ;;
    *) echo "unknown flag $arg" >&2; exit 2 ;;
  esac
done

systemctl daemon-reload
# shellcheck disable=SC2086
systemctl enable $UNITS
echo "enabled: $UNITS (start with: systemctl start <unit>)"
