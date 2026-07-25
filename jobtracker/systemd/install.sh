#!/usr/bin/env bash
# Install the local scan as systemd *user* timers (no root needed).
#
#   ./install.sh            # install + enable + start
#   ./install.sh --status   # show timer state and the last run
#   ./install.sh --remove   # stop, disable and delete the units
#
# User timers only run while the user is logged in unless lingering is
# enabled; the script turns lingering on so scans continue after a reboot
# with nobody at the keyboard. That one step needs sudo — skip it and the
# timers simply wait for the next login.
set -euo pipefail

UNIT_DIR="$HOME/.config/systemd/user"
HERE="$(cd "$(dirname "$0")" && pwd)"
JOBTRACKER="$(dirname "$HERE")"
UNITS=(architecture-jobs-scan.service architecture-jobs-scan.timer
       architecture-jobs-full.service architecture-jobs-full.timer)

case "${1:-}" in
--status)
  systemctl --user list-timers 'architecture-jobs-*' --all
  echo
  systemctl --user status architecture-jobs-scan.service --no-pager || true
  exit 0
  ;;
--remove)
  systemctl --user disable --now architecture-jobs-scan.timer \
                                 architecture-jobs-full.timer 2>/dev/null || true
  for u in "${UNITS[@]}"; do rm -f "$UNIT_DIR/$u"; done
  systemctl --user daemon-reload
  echo "removed."
  exit 0
  ;;
esac

mkdir -p "$UNIT_DIR"
for u in "${UNITS[@]}"; do
  # rewrite the %h-relative paths in case the clone is not at the default
  # location — systemd will not expand a path we guessed wrong
  sed "s#%h/code/code_web/architecture-jobs/jobtracker#$JOBTRACKER#g" \
      "$HERE/$u" > "$UNIT_DIR/$u"
done
chmod +x "$JOBTRACKER/run_local.sh"

systemctl --user daemon-reload
systemctl --user enable --now architecture-jobs-scan.timer architecture-jobs-full.timer

if ! loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q 'Linger=yes'; then
  echo
  echo "Timers installed, but they will pause when you log out."
  echo "To keep scanning across reboots:  sudo loginctl enable-linger $USER"
fi

echo
systemctl --user list-timers 'architecture-jobs-*' --all
echo
echo "Log:      tail -f $JOBTRACKER/scraper.log"
echo "Run once: systemctl --user start architecture-jobs-scan.service"
