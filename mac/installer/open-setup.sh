#!/bin/sh
# open-setup.sh LABEL APP SECONDS: opens Domovoi's Setup once Installer has
# quit. Started by the package's postinstall as a one-shot job (LABEL) in
# the signed-in person's own GUI session, as that person. Installed in
# share/installer/ (mac/build.sh).
#
# Waits while Installer.app runs, SECONDS at most (then gives up and opens
# nothing), opens APP, then unloads its own job: that ends this script, and
# nothing stays loaded. Opening the app only after Installer has quit keeps
# Installer's last step clear of the App Management prompt (P3-1).
#
# DOMOVOI_OPEN_SETUP_TEST_PATH is for mac/tests/installer-scripts.sh.

PATH=${DOMOVOI_OPEN_SETUP_TEST_PATH:-/usr/bin:/bin:/usr/sbin:/sbin}
export PATH

label=${1:-com.domovoi.mac.open-setup}
app=${2:-/Applications/Domovoi.app}
limit=${3:-1800}
case $limit in ''|*[!0-9]*) limit=1800 ;; esac

waited=0
gave_up=""
while pgrep -x Installer >/dev/null 2>&1; do
  if [ "$waited" -ge "$limit" ]; then gave_up=1; break; fi
  sleep 2
  waited=$((waited + 2))
done

if [ -z "$gave_up" ] && [ -d "$app" ]; then
  open "$app" >/dev/null 2>&1
fi

# Last: unload this job (launchd stops this script with it).
launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1
exit 0
