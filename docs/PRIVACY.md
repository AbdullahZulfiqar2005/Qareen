# Privacy and data boundaries

## Defaults and controls

Cloud sharing, Gmail sync, automatic proposals, extension capture, and raw shortcut capture start disabled. Starting the Go collector enables its desktop sources; pause collection or exclude sources in the dashboard. Shortcut capture additionally requires `QAREEN_CAPTURE_KEYBINDS=1` and device access. Never run Qareen as root to acquire input-device access.

Admin and ingest tokens are random, separate capabilities in `~/.local/share/qareen`, with private directory/file permissions. The ingest capability accepts only supported browser sources. The local HTTP service validates Host, Origin, content type, schema, body size, and capability. The dashboard uses `textContent`, a restrictive CSP, and session-only token storage. An authenticated extension or same-user process remains part of the trust boundary. Loopback HTTP is not a defense against a compromised local account.

The collector redacts common passwords, bearer tokens, tokenized URLs and private-key blocks before persistence. The Python ingestion path and outbound model path also redact. This is best effort, not a DLP guarantee. Do not collect highly sensitive sources on that assumption.

Gmail OAuth credentials are stored by the OS keyring. The provider key goes in a private environment file; do not commit it. The `gmail.compose` scope is broader than draft-only: Google also permits sending with that credential. Qareen deliberately has no sending implementation.

## Data inventory

| Data | Location | Deletion |
|---|---|---|
| Events, chunk vectors, proposals, approved examples | `~/.qareen.db` (or `QAREEN_DB_PATH`) | Dashboard delete/retention; dependent records cascade |
| Collector retry files and cursors | `~/.local/share/qareen` (or `QAREEN_DATA_DIR`) | Delete-all removes pending collector outbox; cursors contain IDs/offsets |
| Browser retry queue and ingest token | Extension local storage | Extension options → Delete pending queue; uninstall extension to remove token |
| Optional CLI question history | `QAREEN_DATA_DIR/chat-*.json` | `chat.py --reset`; delete-all removes question sessions |
| Gmail refresh credential | OS keyring, service `qareen`, user `gmail-oauth` | Delete through keyring UI and revoke app access in Google |
| Exported Gmail drafts | Your Gmail account | Delete in Gmail; local deletion never silently deletes remote drafts |
| Optional documentation index | `QAREEN_DATA_DIR/arch_wiki_index` | Remove directory; contains operator-provided documentation |

Delete-all pauses capture and deletes local events, vectors, dependent proposals/feedback, collector outbox, CLI question history, and known legacy event index/log copies. Also pause the extension and clear its queue before deleting: not-yet-delivered events are unknown to the server. Hashed tombstones prevent already-deleted source events from reappearing through retries or Gmail thread resync; they contain no message text. Retention performs the same event/dependency deletion for expired rows.

SQLite uses secure_delete and a WAL checkpoint after deletion. Filesystem snapshots, backups, SSD wear leveling, browser storage, already exported drafts, and provider retention are outside this deletion guarantee. Full-disk encryption is recommended for device-loss protection. Application-level encryption is not implemented.

Stop services before a full manual wipe. Remove the database and its WAL/SHM companions, private data directory, extension data, and credential separately; do not run broad deletion commands without checking your configured paths. `scripts/uninstall.sh` removes app code and services while preserving personal data.
