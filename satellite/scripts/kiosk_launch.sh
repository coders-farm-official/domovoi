#!/bin/sh
# Launch the Domovoi video-satellite kiosk: cage (a wlroots single-window
# Wayland compositor) running Chromium fullscreen on this satellite's
# display page. Executed by domovoi-kiosk.service — not meant to be run by
# hand except for debugging.
#
# The URL is derived by satellite/kiosk.py from ~/.domovoi/config.toml
# ([satellite] domovoi_url + room_id, or the [display] kiosk_url override)
# so the logic lives in tested Python, not here.
#
# Chromium flags:
#   --kiosk                          fullscreen, no chrome
#   --noerrdialogs                   never block the screen on an error box
#   --disable-session-crashed-bubble no "restore pages?" bar after a crash
#   --autoplay-policy=...            allow the page's media session hooks
#   --user-data-dir                  own profile: never shares state (or a
#                                    service worker) with any other browser
# If Chromium is unstable on your board's GPU stack, add --disable-gpu
# (see VIDEO_SATELLITE.md).

set -eu

VENV_PY="${DOMOVOI_VENV_PY:-$HOME/satellite-venv/bin/python}"
URL="$("$VENV_PY" -m satellite.kiosk --print-url)"

BROWSER="${DOMOVOI_KIOSK_BROWSER:-chromium}"
command -v "$BROWSER" >/dev/null 2>&1 || BROWSER=chromium-browser

exec cage -- "$BROWSER" \
  --kiosk \
  --noerrdialogs \
  --disable-session-crashed-bubble \
  --autoplay-policy=no-user-gesture-required \
  --user-data-dir="$HOME/.domovoi/kiosk-profile" \
  "$URL"
