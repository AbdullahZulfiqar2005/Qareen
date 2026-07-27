---
mainfont: "Open Sans"
---

# Qareen (User Digital Twin) — Comprehensive Technical Documentation (Part 1)

Qareen is a local digital twin and RAG-based troubleshooting assistant designed for Linux users, with native support for the Hyprland compositor, SwayNC notifications, Firefox, and various chat/communication web platforms. It captures user activities, terminal commands, active window states, system logs, web interactions, and closed captions from online meetings, embedding them into a local SQLite database for semantic search and AI-assisted troubleshooting.

This document serves as the complete technical manual, architectural guide, and code references of Qareen.

---

## Table of Contents (Part 1 & Part 2)

### [PART 1]
1. **Introduction and Project Philosophy**
   - 1.1 The Concept of a Personal Digital Twin
   - 1.2 Architectural Constraints: Local-first, Low-Resource, Privacy-Conscious
   - 1.3 Target Personas and Use Cases
2. **System-Level Architecture**
   - 2.1 Multi-Component Design & Process Interaction
   - 2.2 Data Flows and IPC Interfaces
   - 2.3 Shared Embedding Model Reuse Strategy
3. **Go CLI & Background Daemon (`main.go`)**
   - 3.1 Subcommand Interface and Daemon Life Cycle
   - 3.2 Threading Tickers and Tracking Loop
   - 3.3 Active Window Tracking via Hyprland socket
   - 3.4 Shell History and Firefox Places Database Reading
   - 3.5 System Error Logs and Journalctl Tracking
4. **Hyprland Keybind Tracker & Input Processing**
   - 4.1 Linux Input Event Architecture (`/dev/input/`)
   - 4.2 Modifier State Machines and Bitmasks
   - 4.3 Keycode Translation and Mapping
   - 4.4 Matching Binds dynamically from `hyprctl binds`
5. **Python Embedding Server (`embedding_server.py`)**
   - 5.1 Multi-Threaded HTTP HTTP-Base Server Architecture
   - 5.2 LangChain & SentenceTransformers Integration
   - 5.3 Database Direct Schema and WAL Connection Management

### [PART 2] (Available in the Next Chapter)
6. **Firefox Session Recovery Parser**
7. **Chrome/Firefox Browser Extension (`extension/`)**
8. **RAG Troubleshooting with Arch-Sage (`chat.py`)**
9. **Arch Wiki Reference Fallback Search**
10. **Setup, Troubleshooting, and Deployment Guide**

---

## 1. Introduction and Project Philosophy

### 1.1 The Concept of a Personal Digital Twin
A "Digital Twin" is traditionally defined in engineering as a virtual representation of a physical object or system, updated from real-time data. Applied to a human desktop user, a Personal Digital Twin is a software agent that continuously observes, indexes, and structures the digital footprints of the user. 

Rather than uploading this footprint to cloud-based time-tracking or monitoring services, Qareen implements this digital twin on local hardware. The twin acts as a contextual memory cache. When the user encounters a recurring issue—such as a configuration error, a broken package, a script failure, or a specialized command sequence they forgot—they can query Qareen. The LLM (powered by Groq) synthesizes past activities into step-by-step instructions.

### 1.2 Architectural Constraints: Local-first, Low-Resource, Privacy-Conscious
The design of Qareen is governed by three strict principles:
1. **Privacy-Conscious Local Storage**: High-frequency desktop logs (such as window focus changes, terminal command history, and browser activities) are extremely sensitive. Qareen stores these events in a local SQLite database (`~/.qareen.db`) with zero external telemetry. The only external API call is to Groq's completions endpoint when a query is executed.
2. **Low CPU and RAM Footprint**: Running sentence embeddings (typically requiring PyTorch) on local machines can easily consume several gigabytes of RAM. Loading separate instances of these models in every CLI tool or script would quickly exhaust system memory. Qareen circumvents this by running a single local Python HTTP server that holds the model in memory and services all other components.
3. **Minimal Disk Write Overhead**: High-frequency logging can cause rapid SSD write wear if not optimized. Qareen implements compact float32 binary packing for embeddings to reduce disk usage by over 80% compared to typical text-based JSON arrays.

### 1.3 Target Personas and Use Cases
Qareen is designed for system administrators, developers, and power users running Linux environments (specifically Arch Linux + Hyprland). Typical use cases include:
- **Command Retrieval**: "How did I run that ffmpeg command to convert that video three weeks ago?"
- **Troubleshooting Recall**: "What did I do to fix my audio drivers the last time they broke after a system update?"
- **Activity Contextualization**: "What was I working on yesterday morning when I was chatting with the database administrator?"

---

## 2. System-Level Architecture

### 2.1 Multi-Component Design & Process Interaction
Qareen is split into three main decoupled layers:
1. **The Go Daemon (`qareen`)**: Written in Go for low memory usage, native OS integration, and speed. It handles low-level Linux inputs, parses journalctl, calls CLI utilities like `hyprctl`, and coordinates background logging.
2. **The Python Embedding Server (`embedding_server.py`)**: A lightweight HTTP service that loads `all-MiniLM-L6-v2` via HuggingFace SentenceTransformers. It serves as the vectorizer for both the Go daemon and auxiliary python scripts (`chat.py`, `hyprland_monitor.py`), preventing model duplication.
3. **The Web Companion Extension (`extension/`)**: A Manifest V3 browser extension that intercepts user inputs, ChatGPT prompts, WhatsApp messages, and Google Meet closed captions, forwarding them directly to the local server.

```mermaid
graph TD
    %% Browser Extension
    subgraph Browser Extension (MV3)
        EXT_C[content.js] -->|Messages| EXT_B[background.js]
    end

    %% Embedding Server
    subgraph Python Backend
        ES[embedding_server.py]
        RE[RemoteEmbeddings in qareen_embeddings.py]
    end

    %% CLI and Daemon
    subgraph Go CLI / Daemon
        CLI[main.go / qareen]
        D_FAST[Fast Tracker: Active Window]
        D_MED[Medium Tracker: Shell Hist / FF Hist]
        D_SLOW[Slow Tracker: FF Tabs]
        D_SYS[SysLog Tracker: journalctl]
        CLI -->|Spawn/Daemon Loop| D_FAST & D_MED & D_SLOW & D_SYS
    end

    %% External Systems & Storage
    subgraph Storage & External APIs
        DB[(SQLite: ~/.qareen.db)]
        FAISS[(FAISS: arch_wiki_index/)]
        GROQ[Groq API: Llama 3.3]
    end

    %% Connections
    EXT_B -->|POST /log-web| ES
    ES -->|Insert Event| DB
    ES -->|GET /firefox-tabs| ES
    D_SLOW -->|GET /firefox-tabs| ES
    D_FAST & D_MED & D_SYS -->|Log Event| DB
    D_FAST & D_MED & D_SYS -->|POST /embed| ES
    
    %% RAG Client Utilities
    chat[chat.py: Arch-Sage] -->|Use| RE
    monitor[hyprland_monitor.py] -->|Use| RE
    RE -->|POST /embed| ES
    
    chat & monitor -->|Retrieve| FAISS
    chat & monitor & CLI -->|Query LLM| GROQ
```

### 2.2 Data Flows and IPC Interfaces
All components communicate locally using HTTP endpoints and file inputs:
- **Port 2846**: The default binding port for the Python embedding server.
- **`/embed` (POST)**: Receives a list of texts and returns a list of float arrays representing semantic vector embeddings.
- **`/log-web` (POST)**: Receives events from the browser extension and writes them directly to the database.
- **`/firefox-tabs` (GET)**: Triggers the Python server to decompress and read the active Firefox session.
- **`/wiki-search` (POST)**: Queries the Arch Wiki FAISS index using the loaded SentenceTransformers model.

### 2.3 Shared Embedding Model Reuse Strategy
When running python scripts, loading the SentenceTransformers library imports PyTorch/TensorFlow, tokenizers, and model weight parameters. This takes 5 to 15 seconds and allocates ~800MB of RAM. 

To solve this, Qareen uses the **RemoteEmbeddings** pattern. In [`qareen_embeddings.py`](file:///home/abdullah/Qareen/qareen_embeddings.py), we override the standard LangChain embeddings class:
```python
class RemoteEmbeddings:
    def _embed(self, texts):
        resp = requests.post("http://127.0.0.1:2846/embed", json={"texts": texts})
        return resp.json()["embeddings"]
```
This forces all client scripts like `chat.py` (Arch-Sage) and `hyprland_monitor.py` to route vector tasks to the main embedding server, reducing startup time to milliseconds and saving gigabytes of memory.

---

## 3. Go CLI & Background Daemon (`main.go`)

### 3.1 Subcommand Interface and Daemon Life Cycle
The Go binary `qareen` is the main entryway for the system. It implements subcommands for starting, stopping, querying, and checking status.

- **`qareen start`**: Detects if the daemon or embedding server is already running using PID files (`/tmp/qareend.pid`, `/tmp/qareen_py.pid`). If the port is not open, it spawns the Python server, waits for it to bind to port 2846, and then spawns the Go tracking process (`qareen run-daemon`).
- **`qareen stop`**: Reads PID files, sends SIGTERM signals to both processes, and polls the operating system (using `proc.Signal(0)`) until they exit and release their resources, preventing zombie ports.
- **`qareen status`**: Checks if PIDs are running, verifies SQLite connections, and displays database statistics (event counts).

### 3.2 Threading Tickers and Tracking Loop
The background tracking loop run by `qareen run-daemon` executes trackers on separate asynchronous intervals (tickers):
- **Fast Ticker (2 seconds)**: Monitors active window title changes.
- **Medium Ticker (5 seconds)**: Checks shell command history files and Firefox browsing database records.
- **SysLog Ticker (15 seconds)**: Searches system logs for critical unit errors.
- **Slow Ticker (30 seconds)**: Scrapes active browser tabs.

### 3.3 Active Window Tracking via Hyprland socket
Active window tracking uses the Hyprland IPC command line tool:
```bash
hyprctl activewindow -j
```
The Go tracking code calls this command, unmarshals the JSON response into a struct containing `class` (app name) and `title` (window title), and compares it to the previous state. If it changes, it logs the event. Special parsing is applied to browser titles (e.g. mapping "WhatsApp Web" to `whatsapp` events, "ChatGPT" to `chatgpt` events, and queries to `google`).

### 3.4 Shell History and Firefox Places Database Reading
- **Shell History**: The daemon tails `~/.zsh_history` and `~/.bash_history` by tracking their byte sizes. Only new bytes appended to the history files are read. The parser strips command timestamps and logs executions.
- **Firefox History**: Reads Firefox's `places.sqlite` database. Because Firefox uses a SQLite WAL journal, directly copying the database is unnecessary. Qareen connects using read-only URI mode (`file:places.sqlite?mode=ro`), which prevents locking out Firefox's write operations.

### 3.5 System Error Logs and Journalctl Tracking
To automatically capture system faults, Qareen executes:
```bash
journalctl --since <last_check_time> -p 3 -o json
```
It queries for logs with priority 3 (errors) or lower since the exact timestamp of the last successful check. The JSON output lines are parsed to extract the message and the systemd unit (`_SYSTEMD_UNIT`), compiling them into structured error logs.

---

## 4. Hyprland Keybind Tracker & Input Processing

### 4.1 Linux Input Event Architecture (`/dev/input/`)
Linux exposes input hardware devices (keyboards, mice, touchpads) as event nodes under `/dev/input/event*`. Any program running under a user in the `input` group can open these character files and stream raw hardware events.

The raw binary struct emitted by the Linux kernel is defined as:
```c
struct input_event {
    struct timeval time; // 16 bytes on 64-bit systems
    unsigned short type; // 2 bytes
    unsigned short code; // 2 bytes
    unsigned int value;  // 4 bytes
};
```
In [`main.go`](file:///home/abdullah/Qareen/main.go), we read this structure in chunks of 24 bytes:
```go
type inputEvent struct {
	TimeSec  int64
	TimeUsec int64
	Type     uint16
	Code     uint16
	Value    int32
}
```

### 4.2 Modifier State Machines and Bitmasks
To identify complex keybinds (e.g. `SUPER + SHIFT + E`), Qareen tracks the state of all system modifiers. It does this by updating a bitmask when modifier keycodes are pressed (value = 1) or released (value = 0):
- **Shift** (bit 0 / mask 1): Keycodes 42, 54
- **Lock/Caps Lock** (bit 1 / mask 2): Keycode 58 (press toggles the bitmask)
- **Control** (bit 2 / mask 4): Keycodes 29, 97
- **Alt** (bit 3 / mask 8): Keycodes 56, 100
- **Super** (bit 6 / mask 64): Keycodes 125, 126

```go
func handleInputEvent(db *sql.DB, ev inputEvent) {
	if ev.Type == 1 { // EV_KEY
		if isModifier(ev.Code) {
			if ev.Code == 58 { // Caps Lock
				if ev.Value == 1 {
					activeModifiers ^= 2
				}
			} else {
				if ev.Value == 1 {
					activeModifiers |= getModifierBit(ev.Code)
				} else if ev.Value == 0 {
					activeModifiers &= ^getModifierBit(ev.Code)
				}
			}
		} ...
```

### 4.3 Keycode Translation and Mapping
When a non-modifier key is pressed (value = 1), its raw keycode is translated into XKB string names (e.g. keycode `16` maps to `"q"`, `38` to `"l"`, `57` to `"space"`, `64` to `"f6"`, and `51` to `"comma"`).

### 4.4 Matching Binds Dynamically from `hyprctl binds`
On start, the daemon executes:
```bash
hyprctl binds -j
```
This retrieves a JSON list of all keybinds configured in `hyprland.conf`. The daemon caches these bindings. When a keypress event is captured:
1. The active modifier bitmask and the lowercase key name are compared against the cached binds.
2. If matched, the command logs the event to the database:
   `[KEYBIND] [hyprland] Keybind triggered: SUPER + E -> exec thunar`
3. Notifications are kept completely silent to avoid spamming the screen during normal typing or window navigation, while maintaining detailed logs in SQLite.

---

## 5. Python Embedding Server (`embedding_server.py`)

### 5.1 Multi-Threaded HTTP Server Architecture
The Python backend uses Python's standard `ThreadingHTTPServer`. This runs the HTTP server inside a pool of concurrent threads. When the Go daemon calls `/embed` to query the database and multiple pages are logged via `/log-web` concurrently, the requests do not block each other, preventing UI stutter.

### 5.2 LangChain & SentenceTransformers Integration
The server instantiates the SentenceTransformers model via LangChain:
```python
from langchain_huggingface import HuggingFaceEmbeddings
model = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
```
This loads the default PyTorch weights of the `all-MiniLM-L6-v2` model. This model has a context window of 256 tokens and outputs a dense vector of **384 float dimensions**, representing the exact semantic meaning of the logged events.

### 5.3 Database Direct Schema and WAL Connection Management
To avoid SQLite concurrency locks (which occur when the Go daemon and the Python server attempt to write to the database at the same time), the Python server initializes a single shared connection with a mutex lock:
```python
_db_lock = threading.Lock()
_db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
```
Whenever a browser event is logged via `/log-web`, the server acquires the lock, inserts the event and its vector blob, and commits:
```python
with _db_lock:
    _db_conn.execute(
        "INSERT INTO events (timestamp, event_type, app, content, embedding) VALUES (?, ?, ?, ?, ?)",
        (timestamp, site, target, db_content, embedding_blob),
    )
    _db_conn.commit()
```
This thread-safe, direct write prevents SQLite database lock-out crashes during periods of high browsing activity.

---

## 6. Firefox Session Recovery Parser

### 6.1 The jsonlz4 Compression Format
Firefox saves session backups and tab details inside JSON files compressed with a custom variant of LZ4, labeled as `.jsonlz4` files (specifically `sessionstore-backups/recovery.jsonlz4` in the user's profile directory).

Unlike standard LZ4 files, Mozilla prefixes these files with a proprietary 8-byte magic header:
`mozLz40\0` (represented as bytes `[109, 111, 122, 76, 122, 52, 48, 0]` in decimal).
The actual LZ4 compressed data follows immediately after this header. The first 4 bytes of this data segment specify the size of the uncompressed JSON payload in little-endian format.

### 6.2 Python-based LZ4 Decompression Logic
In [`embedding_server.py`](file:///home/abdullah/Qareen/embedding_server.py), the `GET /firefox-tabs` route implements the following decompression routine:
```python
with open(session_file, "rb") as f:
    magic = f.read(8)
    if magic == b"mozLz40\0":
        compressed_data = f.read()
        # Decompress raw LZ4 block (excluding the 4-byte size header)
        decompressed_data = lz4.block.decompress(compressed_data)
        session_data = json.loads(decompressed_data.decode("utf-8"))
```
The decompressed string is parsed as a JSON dictionary containing the window and tab state.

### 6.3 Tab Extraction Strategy
The parser traverses the session JSON structure to extract the active page titles and URLs. Since Firefox stores history entries for each tab (allowing back/forward navigation), it uses the tab's `index` field to retrieve the currently visible page:
```python
tabs_list = []
for window in session_data.get("windows", []):
    for tab in window.get("tabs", []):
        entries = tab.get("entries", [])
        if entries:
            # Firefox indices are 1-based; map to 0-based index safely
            i = int(tab.get("index", 1)) - 1
            i = max(0, min(len(entries) - 1, i))
            entry = entries[i]
            tabs_list.append({
                'title': entry.get('title', ''),
                'url': entry.get('url', '')
            })
```
This list is serialized as JSON and served back to the Go tracking daemon `/firefox-tabs` endpoint to identify new tab visits.

---

## 7. Chrome/Firefox Browser Extension (`extension/`)

### 7.1 Architecture of `manifest.json`
The companion extension uses Chrome Manifest V3 for cross-browser compatibility (Firefox 140+ / Chrome).
- **Permissions**: Requests `activeTab` to safely inspect content and hosts permission for `http://127.0.0.1:2846/*` to forward logs to the local Python HTTP server.
- **Content Scripts**: Matches `<all_urls>` and injects [`content.js`](file:///home/abdullah/Qareen/extension/content.js) at `document_end`.
- **Developer Sideloading Settings**: Includes the `browser_specific_settings` block:
  ```json
  "browser_specific_settings": {
    "gecko": {
      "id": "qareen-companion@abdullah.org",
      "data_collection_permissions": {
        "required": [
          "browsingActivity",
          "personalCommunications"
        ]
      }
    }
  }
  ```
  This is required by modern Firefox versions for permanent unpacked/custom local add-on validation and installation.

### 7.2 Interprocess Message Forwarding via `background.js`
Because of browser sandbox restrictions, content scripts running in webpage tabs are blocked from making direct network calls to local HTTP ports on some platforms. To bypass this, [`content.js`](file:///home/abdullah/Qareen/extension/content.js) sends a browser runtime message to [`background.js`](file:///home/abdullah/Qareen/extension/background.js), which executes the HTTP POST request to the local embedding server:
```javascript
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.action === "logWeb") {
        fetch("http://127.0.0.1:2846/log-web", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(message)
        });
    }
});
```

### 7.3 DOM Mutation Observers & Web Scrapers
The content script [`content.js`](file:///home/abdullah/Qareen/extension/content.js) implements modular, domain-specific DOM observers:

#### A. Gmail (`mail.google.com`)
- **Sent Emails**: Intercepts clicks on the "Send" button (`div[role="button"][data-tooltip*="Send"]`). Extracts the recipient email addresses (`span[email]`), subject (`input[name="subjectbox"]`), and compose body (`div[role="textbox"]`).
- **Received Emails**: Listens for SPA URL hash changes (matching inbox/sent thread hashes). Scrapes the opened email's subject (`h2.hP`), sender (`span.gD`), and body content (`.a3s.aiL`).

#### B. ChatGPT & Gemini & Claude & Grok
- **Input Capture**: Intercepts Enter presses and clicks on send buttons (such as `#prompt-textarea`, `button.send-button`, or `[data-testid="send-button"]`).
- **Response Capture**: Watches the DOM for incoming chat message bubbles. It waits for assistant nodes to finish rendering (using delayed timeouts of 1.5s to 2s) to fetch the full markdown output text blocks.

#### C. LinkedIn & Instagram
- **Feeds**: Uses intersection observers or visibility scans to capture posts or reels currently on the screen, logging the post owner's handle and the text description.
- **Direct Messages**: Tracks conversation threads to capture direct messages sent and received.

#### D. Chess.com
- Logs when games start against specific opponents.
- Listens to moves appended in the game move list UI (`.move-list-item`) to save game state, moves, and logs in-game chat messages.

#### E. Reddit
- Captures subreddit names, posts viewed (title & post body), and comments submitted.
- Logs upvotes and downvotes.

#### F. GitHub
- Logs repositories viewed (`/owner/repo`), file trees browsed, pull request/issue details, and comments written in markdown fields.

#### G. Google Meet Closed Captions
- Scrapes live caption blocks (`div[jsname="x37oKu"]`). It parses the active caption tree, resolves which speaker is talking, buffers their words, and logs them when a sentence completes (such as seeing a period, question mark, or length threshold), creating a searchable transcript of meetings.

---

## 8. RAG Troubleshooting with Arch-Sage (`chat.py`)

### 8.1 ReAct Agent Loop
[`chat.py`](file:///home/abdullah/Qareen/chat.py) ("Arch-Sage") runs a LangGraph ReAct agent. It runs in a loop:
1. **Reasoning**: The LLM analyzes the user prompt and decides whether to output a final text answer or call a tool.
2. **Action**: The runtime calls the selected tool and returns the text result.
3. **Observation**: The LLM reviews the tool output and decides the next step, repeating until it can formulate the final solution.

### 8.2 Custom Troubleshooting Tools
Arch-Sage implements four tools to inspect and repair systems:
- **`arch_wiki_search`**: Searches the local Arch Wiki FAISS index.
- **`read_system_file`**: Reads specific lines of a local config file (such as `~/.config/hypr/hyprland.conf` or `/etc/fstab`), avoiding directory reads.
- **`run_diagnostic`**: Executes read-only system commands (e.g. `systemctl status`, `ip a`, `lsusb`, `pgrep`) to review state, restricting file writes.
- **`apply_fix`**: Executes modifying commands. To prevent accidents, it prints a prompt requiring the user to type `y` to approve execution.

---

## 9. Arch Wiki Reference Fallback Search

### 9.1 Embedding the Arch Wiki
The Arch Wiki FAISS index is built once using [`embedding_script.py`](file:///home/abdullah/Qareen/embedding_script.py). It reads local offline Arch Wiki HTML documentation (`/usr/share/doc/arch-wiki/html/en/`), parses the text using LangChain's `BSHTMLLoader`, splits them into chunks of 1,000 characters with a 150-character overlap, and indexes them using a local FAISS database.

### 9.2 Fallback Resolution Strategy
If Qareen's personal SQLite memory timeline does not contain the solution to an issue, the LLM falls back to the local Arch Wiki documentation. 
```
  [User Query] ----> (Query Embedding) ----> [embedding_server.py /wiki-search]
                                                      |
                                                      v
                                            [Arch Wiki FAISS Index]
                                                      |
                                                      v
                                            (Top 3 Reference Docs)
                                                      |
                                                      v
  [SQLite DB] -----> (Top 12 Matches) ----> [Chronological Timeline]
                                                      |
                                                      v
  [Combined Context] = [Memory Timeline] + [Arch Wiki Fallback Reference]
                                                      |
                                                      v
  [Groq Completion]  =================================> [Step-by-Step Fix]
```
The Go daemon fetches the top 3 relevant wiki reference blocks from `/wiki-search` on the embedding server and appends them to the prompt context. If the user's personal memories do not contain the answer, the LLM uses the Arch Wiki documentation to formulate the fix.

---

## 10. Setup, Troubleshooting, and Deployment Guide

### 10.1 Environment Setup
1. **Python Virtual Environment**:
   ```bash
   python -m venv ~/venv
   source ~/venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Groq API Key**: Get a key at https://console.groq.com/keys and export it:
   ```bash
   export GROQ_API_KEY="your_api_key_here"
   ```
3. **Build the Go Binary**:
   ```bash
   go build -o qareen .
   sudo cp qareen /usr/local/bin/
   ```

### 10.2 Sideloading the Firefox Extension
1. Pack the extension using the script:
   ```bash
   ./package_extension.sh
   ```
2. In Firefox:
   - Enter `about:config` in the address bar and set `xpinstall.signatures.required` to `false` (requires Developer, Nightly, or ESR).
   - Enter `about:addons` -> click the gear icon -> select **Install Add-on From File...** -> select `qareen.xpi`.
3. If using standard Firefox:
   - Upload the generated `qareen.xpi` as an **unlisted** extension on the [Mozilla Add-on Developer Hub](https://addons.mozilla.org/developers/) to obtain a signed version that can be permanently installed.

### 10.3 Troubleshooting
- **Embedding Server Inactive**: If the embedding server fails to run, check `~/.qareen.log`. Ensure the python virtual environment is located at `~/venv/bin/python` and that port `2846` is not occupied by a zombie process.
- **Port Conflict**: If port 2846 is occupied:
   ```bash
   fuser -k 2846/tcp
   ```
- **Permission Errors for Input Event Tracker**: Ensure your user is in the `input` group to read `/dev/input/event*` nodes:
  ```bash
  sudo usermod -aG input $USER
  ```
  Log out and log back in to apply group membership updates.

---

## 11. Database Schema and Advanced SQL Operations

### 11.1 SQLite Table Definition
Qareen stores all interaction logs and vector representations inside a single database file located at `~/.qareen.db`. The primary table is `events`.

```sql
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    event_type TEXT, -- e.g. "window", "command", "history", "tab", "keybind", "meet"
    app TEXT,        -- e.g. "kitty", "firefox", "gmail", "chatgpt"
    content TEXT,    -- The raw text description of the event
    embedding BLOB   -- 384-dimensional float32 little-endian vector
);
```

### 11.2 Database Indexing Strategies
To ensure query response times remain under 50ms as the database grows to millions of rows, the database creates composite indices.
```sql
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type_app ON events(event_type, app);
```
These indices ensure that time-range searches (e.g. "show commands executed in the last 2 hours") and app-specific filters do not perform full table scans.

### 11.3 Cosine Similarity Calculations on Binary Blobs
In Go, the 384-dimensional float32 embedding vector is loaded from the SQLite database as a raw byte array (`BLOB`). It is deserialized in memory by casting 4-byte chunks directly into float32 float values:

```go
func cosineSimilarity(a, b []float64) float64 {
	if len(a) != len(b) || len(a) == 0 {
		return 0.0
	}
	var dotProduct, normA, normB float64
	for i := 0; i < len(a); i++ {
		dotProduct += a[i] * b[i]
		normA += a[i] * a[i]
		normB += b[i] * b[i]
	}
	if normA == 0.0 || normB == 0.0 {
		return 0.0
	}
	return dotProduct / (math.Sqrt(normA) * math.Sqrt(normB))
}
```

### 11.4 Analytical SQL Query Examples
For statistical diagnostics, user dashboards, and timeline reports, the SQLite database can be queried directly:

- **Top Active Applications**:
  ```sql
  SELECT app, COUNT(*) as count 
  FROM events 
  GROUP BY app 
  ORDER BY count DESC 
  LIMIT 10;
  ```
- **Hourly Activity Distribution**:
  ```sql
  SELECT strftime('%H', timestamp) as hour, COUNT(*) as count 
  FROM events 
  GROUP BY hour 
  ORDER BY hour ASC;
  ```
- **Cleanup and Truncation Operations (Retention Policy)**:
  To maintain a clean database, logs older than 90 days can be deleted while preserving older query memories:
  ```sql
  DELETE FROM events 
  WHERE timestamp < datetime('now', '-90 days') 
    AND event_type != 'query';
  ```

---

## 12. Hyprland IPC Socket and Event Loops

### 12.1 Dynamic UNIX Sockets
Hyprland exposes two UNIX sockets for inter-process communication:
1. **Command Socket (`.socket.sock`)**: Used to dispatch commands to Hyprland (e.g. switching workspaces, closing windows).
2. **Event Socket (`.socket2.sock`)**: A read-only stream that broadcasts real-time compositor state changes.

These sockets are located under:
`/tmp/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock`

### 12.2 Reading Sockets in Go
The Go daemon connects to the event socket and parses incoming stream lines to trigger trackers:
```go
func listenHyprlandEvents() {
	sig := os.Getenv("HYPRLAND_INSTANCE_SIGNATURE")
	if sig == "" {
		logError("HYPRLAND_INSTANCE_SIGNATURE not found. Not running under Hyprland.")
		return
	}
	socketPath := fmt.Sprintf("/tmp/hypr/%s/.socket2.sock", sig)
	conn, err := net.Dial("unix", socketPath)
	if err != nil {
		logError("Failed to connect to Hyprland event socket: %v", err)
		return
	}
	defer conn.Close()

	reader := bufio.NewReader(conn)
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			logError("Error reading Hyprland socket: %v", err)
			break
		}
		handleHyprlandEventLine(strings.TrimSpace(line))
	}
}
```

### 12.3 IPC Event Format
Events are streamed as raw text lines:
`[event_name]>>[event_arguments]`

- **Active Window Changed (`activewindow>>[class],[title]`)**: Triggered when the user focuses a new window.
- **Workspace Switched (`workspace>>[name]`)**: Broadcasts the ID of the new workspace.
- **Window Closed (`closewindow>>[address]`)**: Logs window termination.

---

## 13. Advanced Browser Caption Scraping

### 13.1 Google Meet Caption Observer
The companion extension's content script inspects the Google Meet DOM structure to capture meeting transcripts. Meeting captions are dynamically populated inside a container matching `div[jsname="x37oKu"]`.

### 13.2 Mutex Buffering and Speaker Separation
To handle overlapping speeches and rapid DOM revisions, the script uses a `MutationObserver`. It processes mutations as follows:
1. **Identify Speaker**: Looks up the parent container elements to resolve the speaker's name in `div.ytS0Oc`.
2. **Sentence assembly**: Selects individual text nodes in `span.YAi3t` and appends incoming characters to a speaker-specific buffer.
3. **Flushing rules**: Flushes sentences when a period or question mark is observed, or if the speaker stops talking for more than 3 seconds.

```javascript
let speakerBuffer = "";
let lastSpeaker = "";

const observer = new MutationObserver((mutations) => {
    for (let mutation of mutations) {
        if (mutation.type === "childList") {
            const speakerNode = document.querySelector("div.ytS0Oc");
            const textNode = document.querySelector("span.YAi3t");
            
            if (speakerNode && textNode) {
                let speaker = speakerNode.innerText;
                let text = textNode.innerText;
                
                if (speaker !== lastSpeaker && speakerBuffer) {
                    flushCaption(lastSpeaker, speakerBuffer);
                    speakerBuffer = "";
                }
                
                speakerBuffer = text;
                lastSpeaker = speaker;
                
                if (text.endsWith(".") || text.endsWith("?")) {
                    flushCaption(speaker, speakerBuffer);
                    speakerBuffer = "";
                }
            }
        }
    }
});

function flushCaption(speaker, text) {
    chrome.runtime.sendMessage({
        action: "logWeb",
        site: "meet",
        target: speaker,
        content: `[CAPTIONS] ${speaker}: ${text}`
    });
}
```

---

## 14. Rofi Prompt Bar and Desktop Integration

### 14.1 The Rofi UI Script
For fast desktop access, Qareen includes a Rofi script inside `rofi_ui/prompt.sh`. When executed, it displays an input bar on the screen. The user types their question, and the script queries Qareen and displays the answer inside a styled Rofi window.

```bash
#!/usr/bin/env bash

# Trigger prompt input
QUERY=$(rofi -dmenu -p "🕵️‍♂️ Ask Qareen" -theme-str 'window { width: 40%; }')

if [ -z "$QUERY" ]; then
    exit 0
fi

# Show loading state
notify-send "Qareen" "Analyzing digital twin..." -i system-search

# Run query and capture output
RESPONSE=$(qareen query "$QUERY" 2>&1)

# Display response in styled rofi list
echo "$RESPONSE" | rofi -dmenu -p "🧠 Qareen Response" -theme-str 'window { width: 60%; height: 50%; }'
```

### 14.2 Hyprland Hotkey Binding
To bind the Rofi prompt bar to a global key shortcut (e.g. `SUPER + F1`), append the following line to `~/.config/hypr/hyprland.conf`:
```ini
bind = SUPER, F1, exec, /home/abdullah/Qareen/rofi_ui/prompt.sh
```

---

## 15. Agentic Reasoning Architecture

### 15.1 LangGraph Architecture in `chat.py`
The "Arch-Sage" script uses LangGraph to manage complex system diagnostics.

```
                  +-------------------+
                  |   User Prompt     |
                  +---------+---------+
                            |
                            v
                  +---------+---------+
                  |   Reasoning Node  |<------------------+
                  +---------+---------+                   |
                            |                             |
                 [Has tool calls?]                        |
                 /               \                        |
               YES                NO                      |
               /                    \                     |
              v                      v                    |
     +--------+--------+    +--------+--------+           |
     |   Tool Executor |    | Final Response  |           |
     +--------+--------+    +-----------------+           |
              |                                           |
              +-------------------------------------------+
```

### 15.2 State Definition and Execution Nodes
The graph state is defined as an array of messages:
```python
from typing import Annotated, Sequence
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
```
Nodes represent processing functions:
- **`agent`**: Invokes the LangChain chat model with the current messages state.
- **`action`**: Executes requested tools and appends results to the chat history.

### 15.3 Vector Retrieval Mathematics (FAISS)
When searching the Arch Wiki offline database via `arch_wiki_search`, the FAISS vector index calculates similarity scores using L2 distance:
$$d(u, v) = \sqrt{\sum_{i=1}^{n} (u_i - v_i)^2}$$
These distances are converted to similarity scores, and documents below a threshold are discarded to prevent hallucinated reference fallbacks.

---

## 16. Threading Models and Signal Lifecycles

### 16.1 Mutex Synchronization in the Go Daemon
The Go tracking daemon processes high-frequency events concurrently. For instance, keyboard events and window focus events stream in from separate file readers and Hyprland sockets. To prevent data corruption, structures like the Hyprland keybind cache use a read-write mutex lock (`sync.RWMutex`):

```go
var (
	cachedBinds []HyprlandBind
	bindsMutex  sync.RWMutex
)

// Reading Binds (RLock)
func getBindsCount() int {
	bindsMutex.RLock()
	defer bindsMutex.RUnlock()
	return len(cachedBinds)
}

// Writing Binds (Lock)
func updateBinds(newBinds []HyprlandBind) {
	bindsMutex.Lock()
	defer bindsMutex.Unlock()
	cachedBinds = newBinds
}
```
Using `RLock` allows multiple reader threads to inspect the cache concurrently, while only blocking when the periodic loop updates the binds, preventing lock contention issues.

### 16.2 Graceful Process Shutdown Lifecycle
To prevent corrupting the SQLite database or leaving PID lock files in `/tmp/`, Qareen intercepts OS signals (`SIGTERM`, `SIGINT`). 

```
  [OS Signal: SIGTERM/SIGINT]
              |
              v
     (Signal Goroutine)
              |
              v
     [Done Channel Closed]
        /             \
       v               v
  (Exit Loop)    (Close SQLite Connection)
       |               |
       v               v
  (Delete PIDs)  (Tear Down UNIX socket)
       \               /
        v             v
       [Clean System Exit]
```

The daemon coordinates shutdown in `main.go`:
```go
func runDaemonLoop() {
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	done := make(chan bool, 1)

	go func() {
		<-sigs
		logInfo("Terminating daemon loop gracefully...")
		done <- true
	}()

	for {
		select {
		case <-done:
			logInfo("Daemon loop exited.")
			return
		case <-time.After(1 * time.Second):
			// Process normal tasks...
		}
	}
}
```

---

## 17. Codebase Directory and Symbol Reference Index

For developers extending Qareen, the table below maps the core files, classes, and primary functions across the repository:

| File Name | Class / Struct / Module | Primary Functions | Description |
| :--- | :--- | :--- | :--- |
| `main.go` | `main` | `startDaemon()`, `stopDaemon()`, `queryDaemon()` | Main daemon coordinator, CLI command router, database interface, and evdev raw keyboard logger. |
| `embedding_server.py` | `EmbeddingHandler` | `do_POST()`, `do_GET()`, `find_firefox_profile()` | A multi-threaded python HTTP server that holds the SentenceTransformers model in memory and logs browser extension events. |
| `chat.py` | `Arch-Sage` | `agent()`, `action()`, `run_diagnostic()` | LangGraph troubleshooting agent equipped with diagnostic tools to inspect system configurations and apply repairs. |
| `content.js` | Browser Content Script | `MutationObserver`, `gmailScraper()`, `meetScraper()` | Scrapes text inputs and DOM updates (ChatGPT/Claude prompts, Gmail bodies, Google Meet captions) and forwards them to `background.js`. |
| `background.js` | Browser Service Worker | `chrome.runtime.onMessage` | Resolves sandbox restrictions by proxying DOM scraper payloads as HTTP POST requests to port 2846. |
| `qareen_embeddings.py` | `RemoteEmbeddings` | `embed_documents()`, `embed_query()` | Reusable embeddings class that redirects vectorize tasks to the local HTTP server, saving system RAM. |
| `package_extension.sh` | Shell script | Direct ZIP execution | Compiles the companion extension into `qareen.xpi` for local developer sideloading. |
| `rofi_ui/prompt.sh` | Shell script | Rofi menu invocation | Renders a styled desktop prompt bar to search Qareen's timeline. |

