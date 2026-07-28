# Qareen - Personal Digital Twin

## What is Qareen?

Qareen (Arabic: قرين, meaning "companion spirit") is a **local-first personal digital twin** for Linux. It runs as a background daemon that silently observes your digital activity — shell commands, browser tabs, chat messages, emails, AI conversations, desktop notifications, file changes, keybinds — and stores everything as searchable semantic vectors in a local database.

When you need to recall something — "how did I fix that WiFi driver issue last week?" — you ask Qareen in natural language. It finds the most relevant moments from your history using vector similarity search, then an LLM synthesizes a step-by-step answer from your own past actions.

**Everything stays on your machine.** The only external call is to Groq's API for the final LLM response. Embeddings, storage, and search are 100% local.

---

## Why I Built This

I kept running into the same problem: I'd fix something — a driver issue, a config tweak, a tricky deploy — and three months later I'd hit the same problem with zero memory of what I did. Browser history is useless for this. Shell history is flat text with no context.

Qareen solves this by capturing **everything** with semantic context, so you can query your own past like a knowledge base.

---

## Architecture Overview

The system has **five decoupled components** that communicate over localhost HTTP and a shared SQLite database:

```
┌──────────────────────────────────────────────────────────┐
│                     User Activity                         │
│  Shell commands, window focus, browser, notifications...  │
└──────────────┬──────────────────────────┬────────────────┘
               │                          │
               ▼                          ▼
  ┌─────────────────────┐    ┌────────────────────────┐
  │   Go Daemon          │    │  Browser Extension      │
  │   (main.go)          │    │  (content.js)           │
  │   8 tracking loops   │    │  11+ site scrapers      │
  │   CLI interface       │    │  MutationObserver       │
  └──────────┬───────────┘    └──────────┬─────────────┘
             │                           │
             │  POST /embed              │  POST /log-web
             ▼                           ▼
  ┌──────────────────────────────────────────────────────┐
  │          Python Embedding Server                      │
  │          (embedding_server.py)                        │
  │          Port 2846                                    │
  │                                                       │
  │  • all-MiniLM-L6-v2 model (384-dim vectors)          │
  │  • FAISS event index (vector search)                  │
  │  • Arch Wiki FAISS index (documentation fallback)     │
  └──────────────────────┬───────────────────────────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │    ~/.qareen.db      │
              │    SQLite database   │
              │    events table      │
              └─────────────────────┘
```

**Why two languages?**
- **Go** for the daemon because it handles concurrency natively (goroutines + tickers) and compiles to a single binary with no runtime dependencies. It also reads raw Linux kernel input events (`/dev/input`) for keybind tracking and uses inotify for file watching — both are low-level Linux syscalls that Go wraps cleanly.
- **Python** for the embedding server because the ML ecosystem lives in Python. The sentence-transformer model, FAISS, and LangChain all have Python-first APIs.

---

## Tech Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| Daemon & CLI | Go (standard library + pure-Go SQLite) | Single binary, no CGO, native concurrency |
| Embeddings | all-MiniLM-L6-v2 via LangChain | Fast, 384-dim vectors, runs on CPU |
| Vector Search | FAISS (Facebook AI Similarity Search) | Sub-linear search over 100K+ vectors |
| Storage | SQLite (WAL mode) | Zero config, single file, concurrent reads |
| LLM | Groq API (Llama 3.3 70B) | Fast inference, free tier available |
| Browser Extension | JavaScript (Manifest V3) | Works in Firefox, DOM scraping |
| Desktop UI | Rofi (Wayland) | Keybind-triggered popup, no terminal needed |
| Dashboard | Embedded HTML/CSS/JS served from Go | No build step, no framework, ships in the binary |
| CI | GitHub Actions | Go tests, Python tests, JS tests, linting |

---

## Database Schema

One table, intentionally flat:

```sql
CREATE TABLE events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP,
    event_type TEXT,      -- "shell", "window", "browser", "tabs", etc.
    app        TEXT,      -- "zsh", "firefox", "WhatsApp", etc.
    content    TEXT,      -- human-readable description of what happened
    embedding  BLOB       -- 384-dim float32 vector, 1536 bytes
);
CREATE INDEX idx_events_timestamp ON events(timestamp);
```

**Embedding format:** Little-endian float32 binary blob. Each `float64` from the model is downcast to `float32` and packed as 4 bytes. A 384-dim vector = 1536 bytes. This is ~5x smaller than storing as a JSON array.

---

## File-by-File Breakdown

### Core Files

#### `main.go` (2038 lines) — The Heart of the System

This is the Go daemon, CLI, and dashboard server. Everything except embeddings and ML lives here.

**CLI Commands:**
- `qareen start` — launches the Python embedding server, then the Go daemon as a background process
- `qareen stop` — sends SIGTERM to both processes, waits for clean shutdown
- `qareen status` — shows if processes are alive + event count from the database
- `qareen query "how did I fix X?"` — semantic search + LLM response
- `qareen list [N]` — shows N most recent events
- `qareen clear` — deletes all events
- `qareen dashboard` — starts the web dashboard on port 2847

**Key Functions:**

| Function | What It Does |
|----------|-------------|
| `startDaemon()` | Starts the Python server (with port polling), then re-execs itself with `run-daemon` |
| `runDaemonLoop()` | The main tracking loop — creates the DB schema, launches goroutines, runs tickers |
| `logEvent()` | Formats text, calls the embedding server, packs the vector as binary, INSERTs into SQLite |
| `queryDaemon()` | Calls `/search` on the embedding server (FAISS), re-sorts results chronologically, fetches Arch Wiki context, streams LLM response from Groq |
| `encodeEmbedding()` | Converts `[]float64` → little-endian float32 BLOB |
| `decodeEmbedding()` | Converts BLOB → `[]float64` (handles both binary and legacy JSON format) |
| `cosineSimilarity()` | Standard dot product / (norm_a × norm_b) |
| `serveDashboard()` | Starts an HTTP server on port 2847 serving embedded static files + REST API |

**Tracking Goroutines (inside `runDaemonLoop`):**

| Tracker | Interval | How It Works |
|---------|----------|-------------|
| Active window | 2 sec | Calls `hyprctl activewindow -j`, parses JSON for title/class |
| Shell history | 5 sec | Tails `~/.zsh_history` and `~/.bash_history` by tracking file byte offsets |
| Firefox history | 5 sec | Reads `places.sqlite` in read-only mode (with copy fallback for locked files) |
| Firefox tabs | 30 sec | Calls `/firefox-tabs` on the Python server (decompresses LZ4 session file) |
| System errors | 15 sec | Runs `journalctl --since <last> -p 3 -o json` for priority-3 (error) entries |
| Keybind tracking | Real-time | Reads raw `/dev/input/event*` files (24-byte Linux `input_event` structs), maintains modifier bitmask, matches against cached `hyprctl binds` output |
| Notifications | Real-time | Runs `dbus-monitor` to eavesdrop on `org.freedesktop.Notifications.Notify` calls |
| File watcher | Real-time | Uses Linux inotify on `~/Downloads`, `~/Documents`, `~/Desktop` with 3-second debouncing |

**Dashboard API Endpoints:**

| Endpoint | Returns |
|----------|---------|
| `GET /api/events?limit=50&offset=0&type=X&app=Y` | Paginated events, newest first, with total count |
| `GET /api/search?q=text&k=12` | Proxies to Python server's FAISS `/search` |
| `GET /api/stats` | Total events, today/this week counts, breakdown by type/app/hour/day |

---

#### `embedding_server.py` (553 lines) — ML and Vector Search

A Python HTTP server running on `127.0.0.1:2846`. Loads the embedding model once at startup and holds it resident.

**Endpoints:**

| Endpoint | Purpose |
|----------|---------|
| `POST /embed` | Takes `{"texts": [...]}`, returns `{"embeddings": [[...], ...]}` |
| `POST /search` | Takes `{"query": "...", "k": 12}`, syncs new events from SQLite, runs FAISS similarity search, returns scored results |
| `POST /log-web` | Receives browser extension events, formats them, embeds, inserts into SQLite AND the FAISS index |
| `POST /wiki-search` | Searches the local Arch Wiki FAISS index |
| `GET /firefox-tabs` | Decompresses Firefox's `recovery.jsonlz4` session file and returns open tabs |

**FAISS Event Index:**
- Stored at `~/.qareen_event_index/` (index.faiss + meta.json)
- Built from all existing events on first startup
- Incrementally updated: new events from `/log-web` are added immediately; events inserted by the Go daemon are synced before each `/search` call
- Background thread saves index to disk every 60 seconds (or every 50 inserts)
- This replaced the old full-table-scan approach in main.go where every query had to decode every single embedding BLOB and compute cosine similarity — now it's sub-linear via FAISS

**Key Functions:**

| Function | Purpose |
|----------|---------|
| `pack_embedding()` | Converts float list → little-endian float32 binary blob |
| `format_web_event()` | Formats browser events into human-readable strings (site-specific logic for AI chats, WhatsApp, Gmail, etc.) |
| `_load_or_build_event_index()` | Loads FAISS index from disk or builds from all events in SQLite |
| `_sync_new_events()` | Reads events added by the Go daemon since last sync, adds them to the FAISS index |
| `_add_event_to_index()` | Adds a single new event to the in-memory FAISS index |
| `_background_index_saver()` | Daemon thread that persists the FAISS index to disk periodically |

---

#### `extension/content.js` (624 lines) — Browser Intelligence

A Manifest V3 content script injected into every webpage. It captures user interactions through three mechanisms:

1. **Keydown listener** — captures Enter key presses on chat inputs (ChatGPT, WhatsApp, Claude, Gemini, Grok, LinkedIn, Instagram, Chess.com, Reddit, GitHub)
2. **Click listener** — captures send-button clicks (Gmail compose, WhatsApp send, ChatGPT send, etc.)
3. **MutationObserver** — watches the DOM for incoming messages and AI responses

**Sites with dedicated scrapers:**

| Site | What's Captured |
|------|----------------|
| ChatGPT | User prompts + assistant responses (with streaming completion detection) |
| Claude | User prompts + responses (`.font-claude-message`) |
| Gemini | User prompts + responses (`message-content` elements) |
| Grok | User prompts & responses (`.markdown` element turns on grok.com) |
| Groq Playground | User prompts & responses (`[data-role="assistant"]` elements on console.groq.com) |
| WhatsApp Web | Outgoing + incoming messages (with self-learning identity name heuristics, checkmarks, and data-id verification) |
| Gmail | Sent emails (to/subject/body) + read emails (on URL change) |
| LinkedIn | Sent DMs + viewed feed posts |
| Instagram | Sent DMs + viewed posts/reels |
| Reddit | Viewed posts + submitted comments |
| GitHub | Repo visits, file views, PR/issue views, submitted comments |
| Google Meet | Closed captions with speaker identification and sentence boundary detection |
| Chess.com | Game starts, move lists, chat messages, and final game outcomes (Won, Lost, Draw) |
| Lichess.org | Game starts, opponent names, and final game outcomes (victorious/defeat/mate/resign/draw) |

**Safety features:**
- `isSensitiveInput()` — skips password, card, CVV, SSN, token, key, and secret fields
- `isDuplicate()` — 3-second deduplication window with a capped Map (100 entries) to prevent flooding
- `watchForStreamCompletion()` — debounce-based approach to capture AI responses only after streaming finishes (1.2s of no DOM mutations = done)

#### `extension/background.js` (32 lines) — Message Relay

Receives `logWeb` messages from content.js and POSTs them to `http://127.0.0.1:2846/log-web`. This is needed because content scripts can't make cross-origin HTTP requests directly.

#### `extension/manifest.json` (35 lines) — Extension Config

Manifest V3, targets Firefox (Gecko). Permissions: `activeTab` only. Host permissions: `127.0.0.1:2846`. Injects content.js into `<all_urls>`.

---

### Supporting Files

#### `chat.py` (183 lines) — Arch-Sage Interactive Troubleshooter

A LangGraph ReAct agent with four tools for interactive system troubleshooting:

| Tool | Purpose |
|------|---------|
| `arch_wiki_search` | Searches the local Arch Wiki FAISS index |
| `read_system_file` | Reads a system config file (with line range) |
| `run_diagnostic` | Runs a diagnostic command (e.g., `systemctl status`, `lspci`) |
| `apply_fix` | Runs a fix command (with user confirmation prompt) |

Uses a hit-and-trial strategy: diagnose → hypothesize → fix → verify → retry.

#### `hyprland_monitor.py` (259 lines) — Crash Report Analyzer

Two modes:
- `--analyze` — finds the latest Hyprland crash report, scans for critical errors, queries Arch Wiki for context, generates a diagnostic report via Groq
- `--monitor` — real-time tail of the Hyprland log, triggers automatic analysis when critical error keywords appear

#### `rofi_ui/` — Desktop Prompt Bar

A keybind-triggered (SUPER+A) Rofi popup for querying Qareen without opening a terminal:
- `qareen-rofi.sh` — orchestrates the flow: prompt entry → "thinking" indicator → query → answer display
- `qareen_ask.py` — calls `qareen query` as a subprocess and captures output
- `qareen-theme.rasi` — custom dark/neon theme with phase-specific colors
- `hyprland-snippet.conf` — Hyprland window rules for float, blur, and animation

#### `qareen_embeddings.py` (48 lines) — Shared Embedding Client

A `RemoteEmbeddings` class that delegates embedding calls to the Python server over HTTP. Used by `chat.py` and `hyprland_monitor.py` to avoid loading a second copy of the 800MB+ model.

#### `qareen_dotenv.py` (49 lines) — Shared .env Loader

Consistent `.env` file lookup across Python scripts. Checks: `QAREEN_ENV_FILE` env var → `~/.config/qareen/.env` → `~/.qareen.env` → `.env`.

#### `embedding_script.py` (35 lines) — Arch Wiki Index Builder

One-time script that downloads Arch Wiki pages, splits them into chunks, embeds them, and builds the FAISS index stored at `arch_wiki_index/`.

---

### Test Files

#### `main_test.go` (183 lines) — Go Unit Tests

Tests for the pure functions that don't need HTTP or database:
- `encodeEmbedding`/`decodeEmbedding` round-trip (verifies binary format correctness)
- Legacy JSON format backward compatibility
- Invalid input handling (empty, nil, odd-length bytes)
- `cosineSimilarity` (identical=1.0, orthogonal=0.0, opposite=-1.0, zero vector=0.0, known angle)
- `getFriendlyKeybindText` (modifier bitmask → "SUPER + CTRL + T" string)
- `translateKeycode` (Linux keycode → key name mapping)

#### `test_embedding_server.py` (111 lines) — Python Unit Tests

Tests for `pack_embedding` and `format_web_event`:
- Binary packing round-trip and size verification
- Content formatting for every site type (ChatGPT prompt/response, WhatsApp outgoing/incoming/group, Gmail passthrough, unknown site fallback, etc.)

#### `extension/test_content.test.js` (147 lines) — Extension Unit Tests

Tests for `isDuplicate` and `isSensitiveInput` using Node's built-in test runner:
- Empty/null/whitespace detection
- 3-second dedup window
- Case-insensitive comparison
- Map eviction at 100 entries
- Sensitive field detection (password, card, CVV, SSN, token, key, secret)

#### `.github/workflows/ci.yml` (47 lines) — CI Pipeline

Four parallel jobs: Go tests (with race detector), Python tests (pytest), Extension tests (node --test), Lint (go vet + ruff).

---

### Dashboard Files

#### `dashboard/index.html` (70 lines)

Single-page app shell with three tabs: Timeline, Search, Analytics. No framework — vanilla HTML.

#### `dashboard/style.css` (357 lines)

Dark theme with CSS custom properties. Event type badges with color coding. CSS-only bar charts (no charting library). Responsive grid layout.

#### `dashboard/app.js` (180 lines)

Vanilla JavaScript SPA:
- **Timeline tab** — paginated event list with type/app filters, event cards with colored badges
- **Search tab** — semantic search with relevance score bars
- **Analytics tab** — stat cards (total/today/week), activity-by-hour chart, events-by-type bars, top-apps bars, 30-day daily trend

All charts are CSS `<div>` bars with proportional widths — zero dependencies.

---

## Data Flow

### Event Ingestion (two paths)

**Path 1 — Go Daemon (system-level events):**
```
Shell command typed → zsh_history file grows → Go daemon detects new bytes →
formats as "[timestamp] [SHELL] [zsh] ls -la" → POST /embed to Python server →
gets 384-dim vector → packs as 1536-byte blob → INSERT into events table
```

**Path 2 — Browser Extension (web events):**
```
User sends WhatsApp message → content.js detects Enter key →
reads input text → sends {site, target, content, sender} to background.js →
background.js POSTs to /log-web → Python server formats as
"Sent WhatsApp message to Alice: hey there" → embeds → packs → INSERT
+ adds to FAISS index
```

### Query Pipeline

```
User: qareen query "how did I fix the wifi driver?"
  │
  ├─ POST /search to Python server
  │   ├─ Sync any new events from SQLite into FAISS index
  │   ├─ Embed the query text
  │   ├─ FAISS similarity search (top 12 matches)
  │   └─ Return scored results
  │
  ├─ Re-sort top 12 matches chronologically (timeline order)
  │
  ├─ POST /wiki-search for Arch Wiki context (top 3 docs)
  │
  ├─ Build prompt: system message + timeline + wiki docs + question
  │
  └─ Stream response from Groq API (Llama 3.3 70B)
      └─ Print to terminal as tokens arrive
```

---

## Key Design Decisions

### Why SQLite, not Postgres/Mongo?

This is a single-user local tool. SQLite is zero-config, stores everything in one file (`~/.qareen.db`), supports concurrent reads via WAL mode, and has no server process. The entire database is portable — copy one file.

### Why embed the model locally instead of using an API?

Privacy. Every event contains personal data (chat messages, browsing history, email content). Sending all of that to an external embedding API defeats the purpose of a local-first tool. The `all-MiniLM-L6-v2` model is only ~80MB and runs fast on CPU.

### Why FAISS instead of sqlite-vec?

sqlite-vec requires CGO (a C compiler in the build chain). The project uses a pure-Go SQLite driver (`glebarez/go-sqlite` wrapping `modernc.org/sqlite`) specifically to avoid CGO — the binary compiles anywhere Go runs without needing a C toolchain. FAISS was already a dependency for the Arch Wiki index, so adding a second index for events was natural.

### Why little-endian float32 blobs instead of JSON arrays?

A 384-dim embedding stored as a JSON array of float64 text is ~7.5KB. The same vector as packed float32 binary is 1.536KB — roughly 5x smaller. At 100K events, that's 150MB vs 750MB of embedding storage. The precision loss from float64→float32 is negligible for similarity search.

### Why Go + Python instead of just one language?

Go is excellent for concurrent system programming (goroutines, tickers, inotify, raw device I/O) and compiling to a static binary. Python owns the ML ecosystem. Rather than fight either language's weaknesses, they communicate over HTTP on localhost. The boundary is clean: Go handles system integration and the CLI, Python handles ML and vector search.

---

## How to Run

```bash
# 1. Set up Python environment
python -m venv ~/venv
source ~/venv/bin/activate
pip install -r requirements.txt

# 2. Set your Groq API key
export GROQ_API_KEY="your-key-here"

# 3. Build the Go binary
go build -o qareen .

# 4. Start tracking
./qareen start

# 5. Query your history
./qareen query "how did I fix the wifi issue?"

# 6. Open the dashboard
./qareen dashboard
# → http://localhost:2847

# 7. Stop
./qareen stop
```

---

## Recent Core Updates (July 2026)

### 1. Systemd User Space Integration
We migrated both Qareen service processes to systemd user space:
- **`qareen.service`**: Systemd user unit that executes the Go daemon (`qareen run-daemon`) and imports the graphical/Wayland environment variables (required for Hyprland/DBus monitor hooks).
- **`qareen-embedding.service`**: User unit that manages the local Python server.
- **PID File Compatibility**: The Go CLI status tracker connects seamlessly with systemd user processes by checking the standard `/tmp/qareend.pid` and `/tmp/qareen_py.pid` lock files.

### 2. PyTorch Memory Optimization
Standard SentenceTransformer setups can occupy upwards of 950MB of RAM. We reduced this active RSS consumption to **under 399MB** using the following low-resource optimizations:
- **AUTOGRAD Disable**: Disabled PyTorch autograd gradients (`torch.set_grad_enabled(False)`).
- **Thread Capping**: Overrode PyTorch default CPU thread-pooling behavior (`OMP_NUM_THREADS=1` and `torch.set_num_threads(1)`).
- **GPU Hiding**: Hid CUDA resources using environment flags (`CUDA_VISIBLE_DEVICES=""`).
- **Resource Constraints**: Capped systemd memory usage configurations (`MemoryHigh=400M`, `MemoryMax=450M`).

### 3. Dynamic Browser Scraping Mechanics
- **Self-Learning User Identity (WhatsApp)**: Outgoing messages on WhatsApp Web no longer display your name above the bubble (they align to the right), and the `.message-out` class is gone. The extension implements a self-learning heuristic: it saves the text and timestamp of the inputs you type, matches them to new DOM bubbles, and dynamically learns your profile identity name.
- **Access Checkmark Status**: Identifies outgoing messages via unique message metadata (`data-id` prefixes starting with `true_`) and checkmark status icons (`msg-check`, `msg-doublecheck-ack`, `msg-time`).
- **Group Tooltip Exclusions**: Excludes group configuration tooltips like `"click here for group info"` from contact/group name parsing.
- **AI Prompt Interceptors**: Bypasses textarea checks on ChatGPT/console.groq.com and uses fallback `.value || .innerText || ""` selectors to successfully grab prompt queries on contenteditable input elements.
- **Chess Gameplay Outcomes**: Runs periodic status checkers to capture finished match results (victory, defeat, mate, resignations, draws) exactly once per game on both Chess.com and Lichess.org.

### 4. Temporal Query Routing & Terminal CLI UI
- **Hybrid Query Routing**: When the user query contains temporal keywords (like "today", "yesterday", "last 2 hours", "summarize my day"), the Python search router fetches chronological logs from SQLite directly, giving the LLM 100% accurate context for your daily timeline instead of retrieving semantically close older queries.
- **Styled Command Output**: Refactored `qareen query` terminal output to display a polished timeline showing tree branches (`├──`, `└──`) and emojis representing each category, and automatically indents streamed digital twin answers on the left.

---

## External Dependencies

| Dependency | What For | Scope |
|-----------|---------|-------|
| Groq API | LLM inference (Llama 3.3 70B) | Only called during `qareen query` |
| all-MiniLM-L6-v2 | 384-dim sentence embeddings | Runs locally, loaded once |
| FAISS | Vector similarity search | Runs locally, in-memory |
| hyprctl | Hyprland IPC (active window, keybinds) | Linux/Hyprland only |
| journalctl | System error log queries | Linux only |
| dbus-monitor | Desktop notification capture | Linux only |
| rofi | Desktop prompt bar UI | Optional |

**The only network call is to Groq.** Everything else is local.

---

## Running Tests

```bash
# Go tests (with race detector)
go test -v -race ./...

# Python tests
pip install -r dev-requirements.txt
python -m pytest test_embedding_server.py -v

# Extension tests (Node 18+)
node --test extension/test_content.test.js
```

---

## Project Stats

| Metric | Value |
|--------|-------|
| Total lines of code | ~5,000 |
| Languages | Go, Python, JavaScript, Bash, CSS |
| Go binary dependencies | 2 (pure-Go SQLite driver, x/sys for inotify) |
| Python dependencies | 11 packages |
| Browser extension scrapers | 12 sites |
| Tracking goroutines | 8 concurrent trackers |
| Embedding dimensions | 384 |
| Embedding storage per event | 1,536 bytes |
| Test files | 3 (Go, Python, JS) |
| CI jobs | 4 (go-test, python-test, js-test, lint) |

---

## Current Limitations (Known, Not Ignored)

| Limitation | Why It Exists | Potential Fix |
|-----------|--------------|--------------|
| No encryption at rest | SQLite doesn't natively encrypt; SQLCipher needs CGO | Use SQLCipher or application-level encryption |
| No log rotation | `~/.qareen.log` grows unbounded | Add logrotate config or size-based rotation |
| No retention policy | Events accumulate forever | Add `qareen prune --older-than 90d` |
| Hyprland/Arch-only | Keybinds and window tracking use Hyprland IPC | Add fallback paths for GNOME/KDE/Sway |
| DOM selector drift | ChatGPT/WhatsApp change their HTML regularly | Move selectors to a config file for easy updates |
| No auth on dashboard | Localhost-only so not exposed, but no protection | Add token-based auth if ever exposed to network |

---

## What I'd Add Next

1. **Encryption at rest** — the database contains highly sensitive personal data
2. **Retention policies** — `qareen prune & clean` commands with age limits
3. **Cross-platform support** — fallback trackers for GNOME, KDE, Sway, macOS
4. **Plugin architecture for scrapers** — declarative config instead of hardcoded DOM selectors
