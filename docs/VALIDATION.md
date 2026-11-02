# Validation record

Validated on 1 November 2026 against the implementation branch, using synthetic data only.

| Check | Result |
|---|---|
| Python store, API, retrieval, approval and Gmail transport regressions | 38 passed with optional dependencies installed (37 plus one optional skip in lightweight CI); includes concurrent ingestion, failure/retry state, replay deletion, context races, malformed requests, role/origin/host checks, HTML extraction, unsent-draft exclusion, and draft-export timeout reconciliation |
| Go collector tests with race detector | 23 passed; redaction/deduplication, pause, history partial lines and rotation, Bash/Zsh timestamps, binary encoding collision, modifier pairs, SQLite FTS5 availability, and existing helpers |
| Go vet and build | Passed |
| Node tests | 6 passed, including the shipped content observer under a DOM fixture and the actual queue helpers |
| Python lint/format, JavaScript syntax, shell syntax, git whitespace | Passed |
| Optional ML/Gmail/wiki dependency installation | Installed together successfully; CPU PyTorch selected; pip dependency check passed |
| Actual MiniLM smoke | Loaded `sentence-transformers/all-MiniLM-L6-v2` on CPU; two 384-dimensional vectors; measured squared norm approximately 1.0000001 |
| LangChain/FAISS contract | Actual RemoteEmbeddings subclass exercised through FAISS with a deterministic transport fixture |
| Dashboard browser smoke | Headless Chromium: login, search, proposal edit/revision/approval, privacy settings, 390px mobile overflow check; zero page errors; screenshot visually inspected |
| Synthetic retrieval/storage benchmark | 10,000 persisted events, 1,024 enriched with demo vectors, 20 exact-topic queries, hit@5 1.0; raw results in `benchmark-synthetic.json` |

The benchmark recorded approximately 988 events/s and 22 ms median / 32 ms p95 retrieval in this environment. It measures synthetic exact-topic lookup with hash vectors and a partially enriched history. It does **not** measure semantic recall, generated-answer factuality, production cloud latency/cost, or performance on an 8 GB laptop. Re-run `scripts/evaluate.py` on the target machine; do not present these figures as general performance guarantees.

## Manual release checks still required

- Install/start/stop/reboot the systemd units on a real user session; verify recovery after service failure.
- Verify Hyprland windows, notification bus availability, input device access if explicitly enabled, Firefox profiles/WAL reads, journal rotation, and shallow inotify directory recreation on the target desktop.
- Load the extension in Firefox, grant only intended host access, inspect live ChatGPT streaming and Meet captions, confirm no typed draft capture, and test offline delivery/reload. Node fixtures do not establish live selector compatibility.
- Use a dedicated Gmail test mailbox to validate OAuth consent, keyring refresh, history expiry/pagination, alias/group classification, changed-thread rejection, and unknown-export reconciliation. No real mailbox was connected and no real draft was created in this validation.
- Test a configured Groq account with consented, non-sensitive fixtures. Provider calls were mocked in regression tests; actual generation was not billed or benchmarked.
- Verify application packaging and choose a distribution license; complete any required Google/Mozilla release review. No signed installer, public extension release, or Google verification is claimed.

Review the explicit feature boundaries in [IMPLEMENTATION.md](IMPLEMENTATION.md) before merging. No test suite proves the absence of all bugs.
