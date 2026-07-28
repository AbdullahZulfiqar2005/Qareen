---
mainfont: "Open Sans"
---

# Qareen — Personal Digital Twin

Qareen logs your local activity (shell commands, active window/browser titles, Firefox history, WhatsApp Web / ChatGPT / Claude / Gemini / Grok / Groq / Google Meet text via a companion extension, system journal errors, desktop notifications, and file creation/deletion in your Downloads/Documents/Desktop), embeds each event, and lets you later ask "how did I fix X before?" — it retrieves the most semantically relevant past events and asks an LLM (Groq) to summarize the steps you took.

Requires `dbus` (for the `dbus-monitor` binary used to perceive desktop notifications) in addition to the Python/Go dependencies below - this is already installed on virtually all Linux desktops.

## Components

| Component              | Role                                                                 |
|-------------------------|----------------------------------------------------------------------|
| `main.go` → `qareen`     | CLI + background daemon. Tracks activity, stores events in SQLite, serves the developer dashboard, and implements a styled terminal output. |
| `embedding_server.py`   | Local HTTP server (127.0.0.1:2846). Embeds text, runs hybrid vector/chronological query routing, and receives browser extension logs. |
| `extension/`            | MV3 browser extension that captures WhatsApp/ChatGPT/Claude/Gemini/Grok/Groq/Gmail/Meet text and forwards it. |
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

# 2. Groq API key (required)
# Drop it in a .env file to be picked up automatically:
mkdir -p ~/.config/qareen
cp .env.example ~/.config/qareen/.env
$EDITOR ~/.config/qareen/.env   # fill in GROQ_API_KEY=your-key-here

# 3. Build the Go binary
go build -o qareen .
sudo mv qareen /usr/local/bin/   # or anywhere on your $PATH

# 4. (Optional) Build the Arch Wiki index once:
python embedding_script.py
```

## Systemd User Services

You can configure Qareen to run automatically in user-space upon login:

1. Copy the services to your systemd user configuration directory:
   ```bash
   mkdir -p ~/.config/systemd/user/
   cp qareen.service qareen-embedding.service ~/.config/systemd/user/
   ```
2. Enable and start the services:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now qareen-embedding.service qareen.service
   ```
3. Control services using standard systemd tools:
   ```bash
   systemctl --user restart qareen.service
   systemctl --user status qareen.service
   ```

## Usage

```bash
qareen start           # starts the embedding server + tracking daemon
qareen status          # checks daemon status and database stats
qareen query "how did I fix the wifi driver issue last time?"
qareen list 50         # lists recent events
qareen stop            # cleanly shuts down running services
```

---

## What's New & Fixed in this update

- **Memory Minimization (Reduced RSS by ~60%)**: Optimized the Python embedding server memory footprint. By disabling PyTorch autograd gradients, avoiding unused GPU allocation (`CUDA_VISIBLE_DEVICES=""`), and limiting threads (`OMP_NUM_THREADS="1"`), RSS memory consumption has dropped from ~950MB to **under 399MB**.
- **Systemd User Integration**: Integrated background processes with systemd user services while maintaining full compatibility with the Go binary commands (e.g. `qareen status`, `qareen stop`) via unified `/tmp` PID tracking locks.
- **Dynamic WhatsApp Scraper**:
  - **Self-Learning User Identity**: The extension automatically identifies your real profile name dynamically by matching new message bubbles against your typed input, allowing accurate outgoing/incoming classification.
  - **Classname Independence**: Avoids hardcoded class selectors (like `.message-out`) and uses checkmark existence checks combined with unique message ID (`data-id` starts with `true_`) for classification.
  - **Noisy Tooltip Filters**: Ignores meta strings like `"click here for contact info"` or `"click here for group info"` in group headers.
- **Temporal Query Routing**: Queries about time ranges (e.g. *"Summarize my day"*, *"what did I do today"*, *"last 2 hours"*) are automatically routed to perform direct SQLite chronological queries, ensuring the LLM is given 100% accurate context for your day's activities.
- **Premium Terminal UI Styling**: The command `qareen query` now prints a highly polished visual timeline showing tree branches (`├──`, `└──`) and icons matching each category (💬 for WhatsApp, 🤖 for ChatGPT/Gemini, 💻 for commands), and indents the LLM streaming guidance to make it easier to read.
- **Expanded AI Scrapers**: Added full user prompt & assistant response observers for Claude, Gemini, Groq Playground, and xAI Grok.
- **Chess Match Outcome Tracking**: Periodically monitors active matches on `chess.com` and `lichess.org` to record your final game outcomes exactly once per match (e.g., `Chess.com match finished: You Won! (by Resignation)` or `Lichess match finished: Black is victorious • Mate`).

---

## Known limitations / ideas for next steps

- **No encryption at rest.** `~/.qareen.db` holds a very sensitive record of everything you've typed, browsed, and said in meetings, in plaintext SQLite.
- **No log rotation.** `~/.qareen.log` grows forever; wire it into `logrotate` or cap it in-app.
- **No retention/pruning policy.** The `events` table grows forever. A `qareen prune --older-than 90d` command would keep the DB size bounded.
- **File watcher is non-recursive.** `startFileWatcher()` watches `~/Downloads`, `~/Documents`, and `~/Desktop` themselves but not their subdirectories.
