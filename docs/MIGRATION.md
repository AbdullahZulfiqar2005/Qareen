# Upgrade from the original prototype

1. Stop the old Python/Go processes and any old user services. Save a consistent SQLite backup using SQLite's backup command, or stop all writers before copying the DB. Backups contain personal data: keep them private.
2. Install this branch. The store adds columns and tables without discarding old events. Old event vectors are reset for enrichment; a separate legacy FAISS event index is never read by the new service.
3. Open the dashboard with the new admin token. Verify counts and capture/cloud settings. Set your timezone.
4. Old timestamps may have mixed provenance. Review the original configuration and explicitly migrate using the actual timezone used for the old rows. This operation cannot infer historical timezone changes or DST ambiguity:

   ```python
   from qareen_client import call
   print(call('/api/migrate', {'timezone': 'Asia/Karachi'}))
   ```

   Do not run this example blindly if your old rows were UTC. Back up first. Legacy rows stay marked until migration. Their original ambiguous timestamps cannot be reconstructed automatically.
5. Remove known old plaintext event-index/log copies after checking that you do not need them. Delete-all also removes `~/.qareen_event_index` and `~/.qareen.log`, but is intentionally destructive to the local history.
6. Replace/reload the extension; the new background queue requires an ingest token and explicit source opt-in. Broad auto-capture adapters from the prototype are not enabled.

The new CLI uses systemd user units, not global `/tmp` PID files. Stop old processes by verified ownership rather than blindly signaling old PID values. Service startup, active-window collection, notifications and shortcut capture must be verified on your actual desktop session. The core server and synthetic demo do not require Hyprland.
