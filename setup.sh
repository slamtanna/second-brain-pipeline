#!/bin/bash
# Install / manage the daily vault pipeline under launchd (per-user agent).
#
#   ./setup.sh install [--at HH:MM] [--email ADDR] [--model NAME]
#                         write config, install the daily job, then probe it
#   ./setup.sh probe      re-check access/auth/Gmail from launchd's context
#   ./setup.sh test-email send one test email through the digest path
#   ./setup.sh runnow     start a real run now (in the background, via launchd)
#   ./setup.sh dry-run    show what a run would ingest, change nothing
#   ./setup.sh status     job state + the last few runs
#   ./setup.sh uninstall  remove the job (config and run history are kept)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.vaultpipeline.daily"
PROBE="com.vaultpipeline.probe"
AGENTS="$HOME/Library/LaunchAgents"
DEST="$AGENTS/$LABEL.plist"
STATE="${VP_STATE:-$HOME/.local/state/vault-pipeline}"
CONF="$STATE/config.env"
mkdir -p "$STATE"

plist() {  # label hour minute args...
  local label=$1 hour=$2 minute=$3; shift 3
  local args="" a
  for a in "$@"; do args="$args    <string>$a</string>
"; done
  local when
  if [ "$hour" = "load" ]; then
    when="  <key>RunAtLoad</key><true/>"
  else
    when="  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$hour</integer><key>Minute</key><integer>$minute</integer></dict>
  <key>RunAtLoad</key><false/>"
  fi
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
$args  </array>
  <!-- launchd, not cron: a run missed while the Mac slept fires on wake. -->
$when
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>StandardOutPath</key><string>$STATE/launchd.out</string>
  <key>StandardErrorPath</key><string>$STATE/launchd.err</string>
</dict>
</plist>
EOF
}

probe() {
  rm -f "$STATE/probe.txt"
  plist "$PROBE" load load /bin/bash "$HERE/run_daily.sh" --probe > "$AGENTS/$PROBE.plist"
  launchctl bootout "gui/$UID/$PROBE" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$AGENTS/$PROBE.plist"
  echo "probing from launchd's context (up to 2 min)..."
  local i
  for i in $(seq 1 120); do
    grep -q '^DONE' "$STATE/probe.txt" 2>/dev/null && break
    sleep 1
  done
  launchctl bootout "gui/$UID/$PROBE" 2>/dev/null || true
  rm -f "$AGENTS/$PROBE.plist"
  if [ ! -f "$STATE/probe.txt" ]; then
    echo "probe produced no output; see $STATE/launchd.err"; return 1
  fi
  grep -v '^DONE' "$STATE/probe.txt" | sed 's/^/  /'
  if grep -q '^vault-access FAIL' "$STATE/probe.txt"; then
    cat <<'EOF'

  macOS is blocking background access to ~/Documents. Fix: System Settings
  → Privacy & Security → Full Disk Access → "+" → press Cmd-Shift-G, type
  /bin/bash, add it and switch it on. (launchd runs the job via /bin/bash,
  and claude inherits its access.) Then run: ./setup.sh probe
EOF
  fi
  if grep -q '^claude-auth FAIL' "$STATE/probe.txt"; then
    cat <<'EOF'

  claude isn't authenticated in the background context. Fix: run
  `claude setup-token`, then add CLAUDE_CODE_OAUTH_TOKEN=<token> to
  ~/.local/state/vault-pipeline/config.env and run ./setup.sh probe
EOF
  fi
  if ! grep -q '^gmail OK' "$STATE/probe.txt"; then
    echo; echo "  Gmail isn't reachable; digests fall back to a macOS notification."
  fi
  grep -q 'FAIL' "$STATE/probe.txt" && return 1 || return 0
}

case "${1:-status}" in
  install)
    shift
    at="06:30"; email=""; model=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --at) at="$2"; shift 2 ;;
        --email) email="$2"; shift 2 ;;
        --model) model="$2"; shift 2 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
      esac
    done
    hour=$((10#${at%%:*})); minute=$((10#${at##*:}))
    if [ ! -f "$CONF" ] && [ -z "$email" ]; then
      echo "first install needs --email ADDR (where the digest goes)" >&2; exit 2
    fi
    if [ ! -f "$CONF" ]; then
      cat > "$CONF" <<EOF
# vault-pipeline settings, read by run_daily.sh. See README.md for all of them.
VP_EMAIL=$email
VP_PYTHON=$(command -v python3)
VP_MODEL=$model
VP_PARALLEL=4
VP_AGENT_BUDGET=10
VP_MERGE_BUDGET=25
VP_EMAIL_QUIET=0
EOF
      echo "wrote $CONF"
    else
      echo "kept existing $CONF (edit it to change settings)"
    fi
    chmod +x "$HERE/run_daily.sh" "$HERE/vp_run.py" "$HERE/vp_lint.py"
    mkdir -p "$AGENTS"
    plist "$LABEL" "$hour" "$minute" /bin/bash "$HERE/run_daily.sh" > "$DEST"
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$DEST"
    printf 'installed %s: daily at %02d:%02d\n' "$LABEL" "$hour" "$minute"
    probe || { echo; echo "Installed, but fix the FAIL lines above before the first run."; exit 1; }
    echo; echo "Ready. Next: ./setup.sh test-email, then ./setup.sh dry-run." ;;
  probe) probe ;;
  test-email)
    set -a; [ -f "$CONF" ] && . "$CONF"; set +a
    "${VP_PYTHON:-python3}" "$HERE/vp_run.py" --test-email ;;
  dry-run)
    set -a; [ -f "$CONF" ] && . "$CONF"; set +a
    "${VP_PYTHON:-python3}" "$HERE/vp_run.py" --dry-run ;;
  runnow)
    launchctl kickstart "gui/$UID/$LABEL"
    echo "started; follow with: tail -f $STATE/daily.log" ;;
  status)
    launchctl print "gui/$UID/$LABEL" 2>/dev/null | grep -E 'state|last exit|runs' \
      || echo "$LABEL not loaded"
    echo "last runs:"; tail -5 "$STATE/runs.log" 2>/dev/null || echo "  (none yet)" ;;
  uninstall)
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    rm -f "$DEST"; echo "removed $LABEL (config and history kept in $STATE)" ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
