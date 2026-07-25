#!/usr/bin/env bash
# Register this machine as a self-hosted GitHub Actions runner for
# petrasvestartas/architecture-jobs and keep it running as a *user* systemd
# service — no root anywhere.
#
#   ./install-runner.sh            # download, register, enable
#   ./install-runner.sh --status   # is it online?
#   ./install-runner.sh --remove   # unregister and delete
#
# Why a user service and not the runner's own ./svc.sh: svc.sh writes a
# system unit and calls sudo. A user unit does the same job, and the runner
# only ever needs this user's permissions.
#
# Requires: gh, logged in with the `repo` scope (gh auth status).
#
# Note: the runner executes any workflow in the repo on this machine. That
# is safe here because the repo is private and single-owner — do not point
# it at a public repo, where anyone's pull request could run code on your
# laptop.
set -euo pipefail

REPO="petrasvestartas/architecture-jobs"
DIR="$HOME/actions-runner"
UNIT="github-runner-architecture-jobs"
UNIT_DIR="$HOME/.config/systemd/user"
LABELS="self-hosted,linux,x64,architecture-jobs"

case "${1:-}" in
--status)
  systemctl --user status "$UNIT" --no-pager || true
  echo
  echo "Runners registered with $REPO:"
  gh api "repos/$REPO/actions/runners" \
     --jq '.runners[] | "  \(.name)  \(.status)  [\(.labels|map(.name)|join(","))]"'
  exit 0
  ;;
--remove)
  systemctl --user disable --now "$UNIT" 2>/dev/null || true
  rm -f "$UNIT_DIR/$UNIT.service"
  systemctl --user daemon-reload
  if [ -f "$DIR/.runner" ]; then
    TOKEN="$(gh api -X POST "repos/$REPO/actions/runners/remove-token" --jq .token)"
    (cd "$DIR" && ./config.sh remove --token "$TOKEN")
  fi
  echo "removed. Delete $DIR by hand if you also want the ~700 MB back."
  exit 0
  ;;
esac

command -v gh >/dev/null || { echo "gh CLI not found" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "run: gh auth login" >&2; exit 1; }

# --- download (skipped when already unpacked) --------------------------
mkdir -p "$DIR"
if [ ! -x "$DIR/config.sh" ]; then
  VERSION="$(gh api repos/actions/runner/releases/latest --jq .tag_name | tr -d v)"
  TARBALL="actions-runner-linux-x64-$VERSION.tar.gz"
  echo "downloading runner $VERSION (~230 MB)"
  curl -fSL -o "$DIR/$TARBALL" \
    "https://github.com/actions/runner/releases/download/v$VERSION/$TARBALL"
  tar xzf "$DIR/$TARBALL" -C "$DIR"
fi

# --- register ----------------------------------------------------------
# The registration token is short-lived (about an hour) and single-use, so
# it is fetched fresh here rather than stored anywhere.
if [ ! -f "$DIR/.runner" ]; then
  echo "registering with $REPO"
  TOKEN="$(gh api -X POST "repos/$REPO/actions/runners/registration-token" --jq .token)"
  (cd "$DIR" && ./config.sh --unattended \
      --url "https://github.com/$REPO" \
      --token "$TOKEN" \
      --name "$(hostname)-arch-jobs" \
      --labels "$LABELS" \
      --work _work \
      --replace)
else
  echo "already registered (delete $DIR/.runner to re-register)"
fi

# --- user service ------------------------------------------------------
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/$UNIT.service" <<EOF
[Unit]
Description=GitHub Actions runner for $REPO
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
ExecStart=$DIR/run.sh
# the runner exits cleanly when it updates itself; always bring it back
Restart=always
RestartSec=15
KillMode=process
KillSignal=SIGTERM
TimeoutStopSec=5min
# uv lives in ~/.local/bin and the workflow needs it to build the venv
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT"
sleep 3
systemctl --user is-active "$UNIT" >/dev/null \
  && echo "runner service is active" \
  || { echo "service failed to start:"; systemctl --user status "$UNIT" --no-pager; }

if ! loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q 'Linger=yes'; then
  echo
  echo "The runner will stop when you log out. To keep it online:"
  echo "  sudo loginctl enable-linger $USER"
fi

echo
gh api "repos/$REPO/actions/runners" \
   --jq '.runners[] | "  \(.name)  \(.status)  [\(.labels|map(.name)|join(","))]"'
echo
echo "Trigger a scan:  gh workflow run 'Job scan (local runner)'"
echo "Watch it:        gh run watch"
