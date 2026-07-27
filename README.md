---
mainfont: "Open Sans"
---

# Qareen — Personal Digital Twin

Qareen logs your own local activity (shell commands, active window/browser
titles, Firefox history, WhatsApp Web / ChatGPT / Claude / Gemini / Grok /
Google Meet text via a companion extension, system journal errors, desktop
notifications, and file creation/deletion in your Downloads/Documents/Desktop),
embeds each event, and lets you later ask "how did I fix X before?" — it
retrieves the most semantically relevant past events and asks an LLM (Groq)
to summarize the steps you took.

Requires `dbus` (for the `dbus-monitor` binary used to perceive desktop
notifications) in addition to the Python/Go dependencies below - this is
already installed on virtually all Linux desktops.

## Components

| Component              | Role                                                                 |
|-------------------------|----------------------------------------------------------------------|
| `main.go` → `qareen`     | CLI + background daemon. Tracks activity, stores events in SQLite.  |
| `embedding_server.py`   | Local HTTP server (127.0.0.1:2846). Embeds text, serves Firefox tabs, receives browser extension logs. |
| `extension/`            | MV3 browser extension that captures WhatsApp/ChatGPT/Meet/general page text and forwards it to `embedding_server.py`. |
| `chat.py`               | Interactive agentic Arch Linux troubleshooter (RAG over the Arch Wiki + shell tools). |
| `hyprland_monitor.py`   | Watches Hyprland logs/crash reports and asks the LLM for a diagnosis. |
| `embedding_script.py`   | One-time offline script that builds the local Arch Wiki FAISS index. |
| `qareen_embeddings.py`  | Shared client so `chat.py`/`hyprland_monitor.py` reuse the already-running embedding server instead of loading the model again. |
| `qareen_dotenv.py`      | Shared `.env` loader used by `chat.py`/`hyprland_monitor.py`/the rofi UI. |
| `rofi_ui/`              | Rofi-based prompt bar for Hyprland - ask your digital twin a question via keybind and get the answer in a styled popup. See `rofi_ui/README.md`. |

## Setup

```bash
# 1. Python deps (use a venv — the daemon expects it at ~/venv)
python -m venv ~/venv
source ~/venv/bin/activate
pip install -r requirements.txt

# 2. Groq API key (required — there is no baked-in default)
# Either export it in your shell profile:
export GROQ_API_KEY="your-key-here"   # get one at https://console.groq.com/keys
# ...or, simpler, drop it in a .env file and it'll be picked up automatically
# by main.go, chat.py, hyprland_monitor.py, and the rofi UI:
mkdir -p ~/.config/qareen
cp .env.example ~/.config/qareen/.env
$EDITOR ~/.config/qareen/.env   # fill in GROQ_API_KEY=your-key-here

# 3. Build the Go binary
go build -o qareen .
sudo mv qareen /usr/local/bin/   # or anywhere on your $PATH

# 4. (Optional, for chat.py / hyprland_monitor.py) build the Arch Wiki index once:
python embedding_script.py

# 5. Load the extension: chrome://extensions -> Developer mode -> Load unpacked -> extension/
```

## Usage

```bash
qareen start           # starts the embedding server + tracking daemon
qareen status
qareen query "how did I fix the wifi driver issue last time?"
qareen list 50
qareen stop
```

## Rofi UI (Hyprland)

A keybind-triggered prompt bar so you don't need a terminal open to ask your
digital twin something. See `rofi_ui/README.md` for full setup; the short
version:

```bash
# requires `qareen start` to already be running, and rofi installed
bind = SUPER, A, exec, ~/Qareen/rofi_ui/qareen-rofi.sh   # add to hyprland.conf
```

Press the keybind, type a question, and the answer comes back from the same
memory-timeline + Groq pipeline `qareen query` uses - so it's logged back
into your history just the same.

## Fixed in this update

- **`qareen start` no longer dies silently if the Python embedding server
  fails to launch.** Previously a missing `~/venv` (or any embedding-server
  startup error) made `startDaemon()` return early, so shell/window/browser/
  log tracking never started either — not just embeddings. Now the Go
  tracking daemon always starts; a broken embedding server only disables
  embeddings, `qareen query`, and browser-extension logging, and prints a
  clear warning explaining why and how to fix it.
- **Claude/Gemini/Grok prompts are now labeled like ChatGPT's.** `embedding_server.py`'s
  `/log-web` handler previously only had special formatting for `chatgpt`/
  `whatsapp`/`google-meet`; everything else (including Claude, Gemini, and
  Grok) fell into a generic, context-free `"Web event on {site}: ..."`. Now
  all four AI assistants get matching `"Prompted X in conversation '...':"` /
  `"X replied in conversation '...':"` formatting, so a stored event
  unambiguously shows whether it was something you typed or the AI's reply.
- **Title-based fallback now recognizes Claude/Gemini/Grok too.** If the
  browser extension isn't installed/running, `main.go`'s active-window title
  matching previously only recognized WhatsApp/ChatGPT/Google tabs. It now
  also tags Claude, Gemini, and Grok tabs instead of logging them as a
  generic window-title change.
- **Fixed a broken chess.com selector in `content.js`** (`'input[placeholder*="chat" Tint]'`
  was invalid CSS and threw a `DOMException` whenever the primary selector
  missed). Replaced with a valid case-insensitive attribute selector.

## New: desktop notifications & file perception

- **Desktop notifications** are now tracked via `dbus-monitor` eavesdropping
  on `org.freedesktop.Notifications.Notify` — this works for dunst, mako,
  swaync, or any other DBus-compliant notification daemon, with no
  daemon-specific integration needed. See `startNotificationWatcher()` in
  `main.go`. Requires the `dbus` package (specifically the `dbus-monitor`
  binary) to be installed; if it's missing, this tracker logs a warning
  once and the rest of Qareen is unaffected.
- **File creation/deletion** in `~/Downloads`, `~/Documents`, and `~/Desktop`
  is now tracked via inotify (`golang.org/x/sys/unix`, no new third-party
  dependency). See `startFileWatcher()` in `main.go`. Directories that don't
  exist on your system are skipped silently; if none of the three exist,
  this tracker logs a warning once and the rest of Qareen is unaffected.
  Content is not read or embedded from these files — only the fact that a
  file appeared or disappeared, and its path, is logged.
- After pulling these changes, run `go mod tidy` once (with normal internet
  access) before `go build` — `golang.org/x/sys` moved from an indirect to a
  direct dependency in `go.mod` since the file watcher now imports it
  directly.

## Known limitations / ideas for next steps

- **No encryption at rest.** `~/.qareen.db` holds a very sensitive record of
  everything you've typed, browsed, and said in meetings, in plaintext
  SQLite. Consider `sqlcipher` or an encrypted home directory if this
  travels with you.
- **No log rotation.** `~/.qareen.log` grows forever; wire it into
  `logrotate` or cap it in-app.
- **No retention/pruning policy.** The `events` table grows forever. A
  `qareen prune --older-than 90d` command would keep the DB (and query
  latency) bounded.
- **Linear-scan similarity search.** Fine at low/mid thousands of events;
  if this grows into the hundreds of thousands, look at `sqlite-vec` or a
  proper vector index rather than scanning + scoring every row per query.
- **No systemd unit.** `qareen start` currently has to be run manually (e.g.
  from a Hyprland `exec-once`); a user systemd service would survive reboots
  and restart on crash automatically.
- **No retry/backoff on the Groq streaming call.** A flaky connection just
  fails the query outright.
- **File watcher is non-recursive.** `startFileWatcher()` watches
  `~/Downloads`, `~/Documents`, and `~/Desktop` themselves but not their
  subdirectories. Extending this to subfolders would mean walking the tree
  and adding a watch per subdirectory (and handling new subdirectories
  created afterward), which was left out for now to keep resource usage
  predictable.
- **Notification eavesdropping depends on session bus policy.** Some
  hardened DBus configurations restrict `eavesdrop='true'` match rules to
  privileged processes. If `dbus-monitor` starts but no notifications ever
  get logged, check your session bus policy (this is not restricted by
  default on typical desktop distros).
- **Recording other people's messages/captions.** The extension logs
  incoming WhatsApp messages and Google Meet captions from other
  participants, not just your own. That's worth being deliberate about —
  some jurisdictions have consent requirements around recording
  conversations, even informally to a personal database.
- **Site DOM selectors will drift.** ChatGPT/Gemini/Claude/WhatsApp
  regularly change their markup, and `content.js`'s selectors are matched
  against a snapshot of each site at one point in time. If message capture
  silently stops working for one platform, that's the most likely cause —
  open the target site's devtools console, check for `Qareen Content
  Script` logs, and update the relevant selector in `content.js`.
- **Re-signing the Firefox extension.** `content.js` was changed as part of
  this update (see fix notes below). Firefox extensions must be
  cryptographically signed by Mozilla to install permanently in release
  Firefox, and that signature covers the exact file contents — so any edit
  to `content.js`/`background.js` invalidates your existing signed `.xpi`.
  After changing the extension, rebuild it (`./package_extension.sh`) and
  re-upload the new `qareen.xpi` at
  https://addons.mozilla.org/developers/ for Mozilla to re-sign, then
  reinstall it. (Firefox Developer Edition with
  `xpinstall.signatures.required` set to `false` skips this, if you use
  that build instead.)
