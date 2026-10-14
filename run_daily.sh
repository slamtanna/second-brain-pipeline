#!/bin/bash
# launchd entry point for the daily vault pipeline (see setup.sh).
#   run_daily.sh           one pipeline run (what the schedule calls)
#   run_daily.sh --probe   check, from launchd's own context, that the job
#                          can read ~/Documents (macOS privacy/TCC), that
#                          claude is logged in, and that Gmail is reachable
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
STATE="${VP_STATE:-$HOME/.local/state/vault-pipeline}"
mkdir -p "$STATE"
if [ -f "$STATE/config.env" ]; then
  set -a; . "$STATE/config.env"; set +a
fi
PY="${VP_PYTHON:-python3}"
VAULT="${VP_VAULT:-$HOME/Documents/Life}"

if [ "${1:-}" = "--probe" ]; then
  out="$STATE/probe.txt"; : > "$out"
  if ls "$VAULT/raw/clippings" >/dev/null 2>&1 && head -1 "$VAULT/CLAUDE.md" >/dev/null 2>&1; then
    echo "vault-access OK" >> "$out"
  else
    echo "vault-access FAIL $(ls "$VAULT" 2>&1 | head -1)" >> "$out"
  fi
  r=$(claude -p --model haiku --tools "" --strict-mcp-config "Reply with the single word OK" </dev/null 2>&1 | tail -1)
  echo "claude-auth $( [ "$r" = "OK" ] && echo OK || echo "FAIL $r")" >> "$out"
  # The prompt goes right after -p: --allowedTools is variadic and would
  # swallow a trailing prompt as a tool name.
  r=$(claude -p "Call the Gmail list_labels tool once. Reply with only OK, or FAIL and the reason." \
      --model haiku --tools "" --permission-mode dontAsk --permission-prompts none \
      --allowedTools mcp__claude_ai_Gmail__list_labels </dev/null 2>&1 | tail -1)
  echo "gmail $r" >> "$out"
  echo "DONE" >> "$out"
  exit 0
fi

echo "=== $(date '+%F %T') run ===" >> "$STATE/daily.log"
# caffeinate -i: don't let the Mac idle-sleep mid-run.
caffeinate -i "$PY" "$HERE/vp_run.py" >> "$STATE/daily.log" 2>&1
rc=$?
echo "exit=$rc" >> "$STATE/daily.log"
exit $rc
