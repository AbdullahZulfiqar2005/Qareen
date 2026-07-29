#!/usr/bin/env bash
#
# Qareen rofi launcher.
#
# Bind this to a Hyprland keybind, e.g. in hyprland.conf:
#   bind = SUPER, A, exec, ~/Qareen/rofi_ui/qareen-rofi.sh
#
# Flow: prompt entry -> "thinking" indicator -> full answer window.
# Every question is logged into ~/.qareen.db by `qareen query` itself, so
# the digital twin's memory keeps growing every time you use this.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
THEME="$SCRIPT_DIR/qareen-theme.rasi"
ASK_SCRIPT="$SCRIPT_DIR/qareen_ask.py"

# Prevent piling up multiple instances if the keybind is mashed.
LOCK_FILE="/tmp/qareen-rofi.lock"
if [ -f "$LOCK_FILE" ] && kill -0 "$(cat "$LOCK_FILE" 2>/dev/null)" 2>/dev/null; then
    exit 0
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

if ! command -v rofi >/dev/null 2>&1; then
    notify-send "Qareen" "rofi is not installed." 2>/dev/null || true
    exit 1
fi

# ── Per-phase theme overrides ───────────────────────────────────────
# Each rofi invocation gets a distinct visual identity via -theme-str
# without needing separate .rasi files.

# Thinking phase: purple accent border + purple message text (signals "processing")
THINK_STYLE='
window { border-color: #bb9af7; width: 600px; }
mainbox { padding: 16px; }
message { border-color: #bb9af74d; }
textbox { text-color: #bb9af7; font: "JetBrainsMono Nerd Font Bold 12"; }
'

# Answer phase: wider window with more breathing room for reading
ANSWER_STYLE='
window { width: 800px; height: 650px; }
mainbox { padding: 24px; spacing: 16px; }
message { padding: 20px 24px; }
textbox { font: "JetBrainsMono Nerd Font 11"; }
'

# Error phase: urgent red border + red message text
ERROR_STYLE='
window { border-color: #f7768e; width: 720px; height: 650px; }
mainbox { padding: 20px; }
message { border-color: #f7768e4d; }
textbox { text-color: #f7768e; }
'

# 1. Grab the prompt. No stdin entries are piped in on purpose - this is a
#    free-text prompt, not a picker, so rofi should always accept whatever
#    was typed rather than requiring it to match a list item.
prompt="$(rofi -dmenu -theme "$THEME" -p "󰚩 Qareen" -l 0 \
    -theme-str 'entry { placeholder: "Ask your digital twin anything..."; }' \
    < /dev/null || true)"

if [ -z "${prompt// }" ]; then
    exit 0
fi

# 2. Show a "thinking" indicator while the query runs in the background.
rofi -theme "$THEME" -theme-str "$THINK_STYLE" \
    -e "  Querying your memory timeline..." &
THINK_PID=$!

# 3. Run the actual query (embedding similarity + Arch Wiki fallback + Groq),
#    reusing the existing `qareen query` pipeline via qareen_ask.py.
set +e
answer="$(python3 "$ASK_SCRIPT" "$prompt" 2>&1)"
status=$?
set -e

# 4. Close the "thinking" popup now that we have a result.
kill "$THINK_PID" 2>/dev/null || true
wait "$THINK_PID" 2>/dev/null || true

if [ $status -ne 0 ]; then
    rofi -theme "$THEME" -theme-str "$ERROR_STYLE" \
        -e "  $answer"
    exit 0
fi

# 5. Show the final answer. rofi -e wraps long text and closes on
#    click/Escape/Enter.
rofi -theme "$THEME" -theme-str "$ANSWER_STYLE" -e "$answer"
