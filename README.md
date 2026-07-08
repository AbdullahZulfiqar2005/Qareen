# Qareen — Personal Digital Twin

Qareen logs your own local activity (shell commands, active window/browser
titles, Firefox history, WhatsApp Web / ChatGPT / Google Meet text via a
companion extension, and system journal errors), embeds each event, and
lets you later ask "how did I fix X before?" — it retrieves the most
semantically relevant past events and asks an LLM (Groq) to summarize the
steps you took.

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

## Setup

```bash
# 1. Python deps (use a venv — the daemon expects it at ~/venv)
python -m venv ~/venv
source ~/venv/bin/activate
pip install -r requirements.txt

# 2. Groq API key (required — there is no baked-in default)
export GROQ_API_KEY="your-key-here"   # get one at https://console.groq.com/keys

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
- **Recording other people's messages/captions.** The extension logs
  incoming WhatsApp messages and Google Meet captions from other
  participants, not just your own. That's worth being deliberate about —
  some jurisdictions have consent requirements around recording
  conversations, even informally to a personal database.
