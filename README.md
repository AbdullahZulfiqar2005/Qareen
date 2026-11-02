# Qareen

Local activity memory and a **proposal-only assistant** for Linux developers. Qareen collects selected desktop events, retrieves evidence from your history, and drafts replies or incident advice for your review. It never sends a message or executes a proposed command.

This is an engineering preview, with a runnable synthetic demo and regression tests. Live Gmail OAuth, Hyprland, and browser behavior require validation on your machine before everyday use.

## What works

- Go collectors for Bash/Zsh history, Firefox history/tabs, desktop notifications, shallow file events, journal errors, and Hyprland windows. Shortcut capture is separately opt-in.
- SQLite WAL storage with stable source identities, UTC timestamps, FTS5, retryable embedding enrichment, redaction, retention, deletion, and export.
- Local keyword + vector retrieval with event citations, bounded candidate search, interval-aware summaries, and explicit coverage. Local embeddings use one warm MiniLM model; cloud answers use Groq only for enabled sources.
- Background proposal jobs with budgets, retries, approval revisions, expiry, stale-context invalidation, and feedback from approved edits.
- Gmail history sync through the official API. Approved replies can be exported to Gmail **Drafts**. No send endpoint is implemented.
- Firefox companion: opted-in ChatGPT rendered messages, explicit WhatsApp message selections, and partial visible Meet captions. A durable, bounded local queue retries delivery.
- Authenticated dashboard with memory search, proposal review, privacy settings, source filters, and service/usage status.

Qareen retrieves past behavior; it does **not** fine-tune a model or prove that a past command fixed a problem. An observed command, received message, and approved proposal are separate facts.

## Try it without personal data or API keys

Requirements: Linux, Python 3.12+, and a browser. Go is needed only for desktop collectors/CLI; use the version in `go.mod`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p /tmp/qareen-demo
chmod 700 /tmp/qareen-demo
export QAREEN_DATA_DIR=/tmp/qareen-demo/private
export QAREEN_DB_PATH=/tmp/qareen-demo/events.db
.venv/bin/python scripts/demo.py --db "$QAREEN_DB_PATH"
.venv/bin/python embedding_server.py --demo
```

Open `http://127.0.0.1:2846`. In another terminal, read `/tmp/qareen-demo/private/admin.token` and paste it into the dashboard. Try “SQLite”, review the synthetic WhatsApp proposal, edit it, then approve the new revision. The demo provider is confined to fixture creation; the service does not silently fake AI answers. Demo vectors are deterministic word hashes, **not semantic embeddings**.

## Install for your desktop

Back up an existing database and stop older Qareen processes first. See [migration](docs/MIGRATION.md).

```bash
scripts/install.sh --ml    # or --demo for the lightweight backend
~/.local/bin/qareen start
~/.local/bin/qareen doctor
~/.local/bin/qareen token admin
```

The installer creates user services but does not start or enable collection automatically. `qareen start` starts collection. Open the dashboard on port 2846; `qareen dashboard` optionally serves the same UI on port 2847. `qareen pause`, `qareen resume`, and `qareen stop` control collection. Use `systemctl --user enable qareen.service qareen-embedding.service` only if you want login startup.

For cloud generation, put `GROQ_API_KEY=...` in the private `~/.config/qareen/environment`, restart the embedding service, then explicitly enable cloud sharing for selected sources in the dashboard. Capture and cloud sharing are separate controls. Default cloud budget: 20 calls/day; default retention: 90 days. Set your timezone in the dashboard.

For Gmail, install `requirements-gmail.txt` in the application's venv and follow [Gmail setup](docs/GMAIL.md). Enable automatic proposals only after reviewing the manual workflow.

For Firefox, load `extension/manifest.json` through `about:debugging` for temporary testing, open its options, enter `qareen token ingest`, and opt in to sources. `./package_extension.sh` packages all extension assets; permanent Firefox installation requires Mozilla signing. Chromium packaging is not included.

## Development and validation

```bash
.venv/bin/pip install -r dev-requirements.txt
go test -race ./...
go vet ./...
go build ./...
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
node --test extension/test_content.test.js
# Optional real-browser dashboard check:
npm ci
npx playwright install chromium --only-shell
npm run test:ui
.venv/bin/python scripts/evaluate.py --events 10000
```

[Architecture](docs/ARCHITECTURE.md) · [Privacy and data boundaries](docs/PRIVACY.md) · [Review fixes and limitations](docs/IMPLEMENTATION.md) · [Validation record](docs/VALIDATION.md)

## Limits to understand

Qareen is not complete system-wide recording. History may be unflushed, notifications incomplete, and DOM selectors unstable. WhatsApp assistance requires selecting a one-to-one incoming message and confirming its contact identifier; it does not scrape an entire account. Gmail's initial import is bounded to 50 messages and 30 messages per thread. Browser adapters for other providers are not enabled in this preview.

Local data is plaintext with private filesystem permissions. Redaction is best effort. Optional cloud generation shares selected evidence, questions, preferences, and approved examples. Semantic candidates are capped at 5,000 matching recent events; keyword search covers the full retained history. Read the privacy document before collecting sensitive data.

The repository does not currently declare a distribution license. Its owner should choose one before inviting third-party redistribution; this implementation does not make that choice on their behalf.
