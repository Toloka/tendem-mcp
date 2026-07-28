#!/usr/bin/env bash
#
# PostToolUse hook for the Tendem plugin: fires an OS notification when a task
# starts needing the user (quote ready, top-up needed, input needed, result
# ready). Runs after every Tendem MCP tool call; deduped per task on
# next_action change, so 30s long-polls don't spam.
#
# Hooks cannot call MCP tools — this only reacts to the tool result on stdin.
# It never blocks the tool call and always exits 0.
#
# Config:
#   TENDEM_NO_NOTIFY=1  disable notifications (the hook then does nothing)

set -euo pipefail

[ "${TENDEM_NO_NOTIFY:-0}" = "1" ] && exit 0

INPUT="$(cat)"

# jq is required; if absent, no-op silently so nothing breaks.
command -v jq >/dev/null 2>&1 || exit 0

# --- Shape-agnostic field extraction ----------------------------------------
# The tool result's layout differs between clients/namespaces (structured vs.
# text content, embedded JSON strings). Search the whole payload recursively,
# parsing embedded JSON strings too. task_id may only be present in
# tool_input; the search covers that as well.
deep_scalar() { # $1 = key -> last matching string/number value (or empty)
  printf '%s' "$INPUT" | jq -r --arg k "$1" '
    def deep(k): (.. | objects | select(has(k)) | .[k]),
                 (.. | strings | (fromjson? // empty) | deep(k));
    [deep($k) | select(type=="string" or type=="number")] | last // empty
  ' 2>/dev/null || true
}

NEXT="$(deep_scalar next_action)"
[ -n "$NEXT" ] || exit 0

# --- Dedup: notify only when next_action changes for this task ---------------
TASK_ID="$(deep_scalar task_id)"
STATE_DIR="${PLUGIN_DATA:-${TMPDIR:-/tmp}}/tendem-hook"
STATE_FILE="$STATE_DIR/${TASK_ID:-unknown}.last"
LAST=""; [ -f "$STATE_FILE" ] && LAST="$(cat "$STATE_FILE" 2>/dev/null || true)"
[ "$NEXT" = "$LAST" ] && exit 0
mkdir -p "$STATE_DIR" 2>/dev/null || true
printf '%s' "$NEXT" > "$STATE_FILE" 2>/dev/null || true

case "$NEXT" in
  await_user_approval) MSG="A task quote is ready — approval needed." ;;
  await_user_topup)    MSG="Top-up needed to approve a task." ;;
  await_input)         MSG="Tendem is waiting for input." ;;
  fetch_result)        MSG="A task result is ready to fetch." ;;
  *) exit 0 ;;
esac

# --- Notifier: macOS / Linux / Windows, best-effort, silent skip -------------
notify() {
  local title="$1" msg="$2"
  if command -v osascript >/dev/null 2>&1; then
    # macOS
    title="${title//\"/\\\"}"; msg="${msg//\"/\\\"}"
    osascript -e "display notification \"${msg}\" with title \"${title}\"" >/dev/null 2>&1 || true
  elif command -v notify-send >/dev/null 2>&1; then
    # Linux desktops
    notify-send "$title" "$msg" >/dev/null 2>&1 || true
  elif command -v powershell.exe >/dev/null 2>&1; then
    # Windows (git-bash / WSL with interop): plain toast, no extra modules
    title="${title//\'/\'\'}"; msg="${msg//\'/\'\'}"
    powershell.exe -NoProfile -NonInteractive -Command "
      [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;
      \$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);
      \$t = \$x.GetElementsByTagName('text');
      \$t.Item(0).AppendChild(\$x.CreateTextNode('$title')) | Out-Null;
      \$t.Item(1).AppendChild(\$x.CreateTextNode('$msg')) | Out-Null;
      [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Tendem').Show([Windows.UI.Notifications.ToastNotification]::new(\$x));
    " >/dev/null 2>&1 || true
  fi
}

notify "Tendem" "$MSG"
exit 0
