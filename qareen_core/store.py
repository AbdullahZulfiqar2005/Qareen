import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import sqlite3
import struct
import threading
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import ACTIONS, DEFAULT_SETTINGS, SOURCES, db_path, redact, validate_settings


def utcnow():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def timestamp(value=None):
    if not value:
        return utcnow()
    d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError("Source timestamps require a timezone")
    return d.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Store:
    """SQLite is authoritative, including vectors. No stale separate event cache."""

    def __init__(self, path=None):
        self.path = Path(path or db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("Database must not be a symlink")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        self.lock = threading.RLock()
        lockfd = os.open(
            str(self.path) + ".migration.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX)
            self.db = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA busy_timeout=10000")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA secure_delete=ON")
            self.migrate()
        finally:
            fcntl.flock(lockfd, fcntl.LOCK_UN)
            os.close(lockfd)

    @contextlib.contextmanager
    def transaction(self):
        with self.lock, self.db:
            yield self.db

    def migrate(self):
        with self.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, event_type TEXT, app TEXT, content TEXT, embedding BLOB)"
            )
            columns = {r[1] for r in db.execute("PRAGMA table_info(events)")}
            extra = {
                "source_event_id": "TEXT",
                "account_id": "TEXT DEFAULT ''",
                "conversation_id": "TEXT DEFAULT ''",
                "actor_id": "TEXT DEFAULT ''",
                "action": "TEXT DEFAULT 'observed'",
                "direction": "TEXT DEFAULT 'unknown'",
                "ingested_at": "TEXT",
                "metadata": "TEXT DEFAULT '{}'",
                "embedding_version": "TEXT",
                "embedding_status": "TEXT DEFAULT 'pending'",
                "embedding_attempts": "INTEGER DEFAULT 0",
                "next_embedding_at": "TEXT",
                "schema_version": "INTEGER DEFAULT 2",
            }
            for name, typ in extra.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE events ADD COLUMN {name} {typ}")
            db.executescript("""
                CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_events_source ON events(event_type,account_id,source_event_id) WHERE source_event_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_events_conversation ON events(conversation_id);
                CREATE TABLE IF NOT EXISTS event_tombstones(key TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS connector_cursors(connector TEXT PRIMARY KEY,cursor TEXT,last_success TEXT,health TEXT);
                CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,kind TEXT,event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,status TEXT DEFAULT 'pending',attempts INTEGER DEFAULT 0,next_attempt_at TEXT,lease_until TEXT,last_error TEXT,UNIQUE(kind,event_id));
                CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY,type TEXT,source_refs TEXT NOT NULL,recipient_ids TEXT NOT NULL,content TEXT NOT NULL,revision INTEGER DEFAULT 1,status TEXT DEFAULT 'proposed',created_at TEXT,expires_at TEXT,provider_draft_id TEXT,export_state TEXT,context_version TEXT);
                CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY,proposal_id TEXT REFERENCES proposals(id) ON DELETE CASCADE,decision TEXT,final_text TEXT,reason TEXT,created_at TEXT);
                CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY,action TEXT,object_id TEXT,revision INTEGER,timestamp TEXT,outcome TEXT);
                CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY,day TEXT,kind TEXT,input_tokens INTEGER DEFAULT 0,output_tokens INTEGER DEFAULT 0,latency_ms INTEGER DEFAULT 0,status TEXT);
                CREATE TABLE IF NOT EXISTS migration_state(key TEXT PRIMARY KEY,value TEXT);
                CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(content,app,content='events',content_rowid='id');
                CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN INSERT INTO events_fts(rowid,content,app) VALUES(new.id,new.content,new.app); END;
                CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN INSERT INTO events_fts(events_fts,rowid,content,app) VALUES('delete',old.id,old.content,old.app); END;
                CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE OF content,app ON events BEGIN INSERT INTO events_fts(events_fts,rowid,content,app) VALUES('delete',old.id,old.content,old.app); INSERT INTO events_fts(rowid,content,app) VALUES(new.id,new.content,new.app); END;
            """)
            if not db.execute("SELECT 1 FROM migration_state WHERE key='v2'").fetchone():
                # Preserve old content/timestamps until an explicit timezone migration.
                if "schema_version" not in columns:
                    db.execute(
                        "UPDATE events SET schema_version=1,embedding_status='pending',embedding=NULL,embedding_version=NULL"
                    )
                db.execute("INSERT INTO events_fts(events_fts) VALUES('rebuild')")
                db.execute("INSERT INTO migration_state VALUES('v2',?)", (utcnow(),))
            for key, value in DEFAULT_SETTINGS.items():
                db.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    def close(self):
        with self.lock:
            self.db.close()

    def settings(self):
        with self.lock:
            return {
                r["key"]: json.loads(r["value"]) for r in self.db.execute("SELECT * FROM settings")
            }

    def update_settings(self, values):
        values = validate_settings(values)
        with self.transaction() as db:
            for key, val in values.items():
                db.execute("UPDATE settings SET value=? WHERE key=?", (json.dumps(val), key))
        return self.settings()

    def ingest(self, event):
        if not isinstance(event, dict):
            raise ValueError("Event must be an object")
        source = event.get("event_type", event.get("site", ""))
        if source not in SOURCES:
            raise ValueError("Unknown event source")
        settings = self.settings()
        if settings["paused"] or source in settings["excluded_sources"]:
            return {"ignored": True}
        raw = event.get("content")
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 100000:
            raise ValueError("Content must contain 1–100000 characters")
        content = redact(raw.strip())
        action = event.get("action", "observed")
        if action not in ACTIONS:
            raise ValueError("Unknown event action")
        direction = event.get("direction", "unknown")
        if direction not in {"incoming", "outgoing", "unknown"}:
            raise ValueError("Invalid direction")
        fields = {}
        for key in ("account_id", "conversation_id", "actor_id", "source_event_id"):
            v = event.get(key, "")
            if not isinstance(v, str) or len(v) > 1000:
                raise ValueError("Invalid " + key)
            fields[key] = redact(v)
        app = redact(event.get("app", event.get("target", "")))[:1000]
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict) or len(json.dumps(metadata)) > 10000:
            raise ValueError("Invalid metadata")
        metadata = json.loads(redact(json.dumps(metadata)))
        source_id = fields["source_event_id"] or str(uuid.uuid4())
        occurred = timestamp(event.get("occurred_at"))
        with self.transaction() as db:
            key = hashlib.sha256(
                (source + "\0" + fields["account_id"] + "\0" + source_id).encode()
            ).hexdigest()
            if db.execute("SELECT 1 FROM event_tombstones WHERE key=?", (key,)).fetchone():
                return {"ignored": True, "deleted": True}
            old = db.execute(
                "SELECT id,content FROM events WHERE event_type=? AND account_id=? AND source_event_id=?",
                (source, fields["account_id"], source_id),
            ).fetchone()
            if old:
                if event.get("revision_update") is True and old["content"] != content:
                    db.execute(
                        "UPDATE events SET content=?,embedding=NULL,embedding_version=NULL,embedding_status='pending',embedding_attempts=0,next_embedding_at=NULL,metadata=? WHERE id=?",
                        (content, json.dumps(metadata), old["id"]),
                    )
                    self.invalidate_proposals(old["id"], db)
                return {"id": old["id"], "duplicate": True}
            cur = db.execute(
                """INSERT INTO events(timestamp,event_type,app,content,source_event_id,account_id,conversation_id,actor_id,action,direction,ingested_at,metadata)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    occurred,
                    source,
                    app,
                    content,
                    source_id,
                    fields["account_id"],
                    fields["conversation_id"],
                    fields["actor_id"],
                    action,
                    direction,
                    utcnow(),
                    json.dumps(metadata),
                ),
            )
            eid = cur.lastrowid
            if fields["conversation_id"]:
                for previous in db.execute(
                    "SELECT id FROM events WHERE event_type=? AND account_id=? AND conversation_id=? AND id<>?",
                    (source, fields["account_id"], fields["conversation_id"], eid),
                ).fetchall():
                    self.invalidate_proposals(previous[0], db)
            if settings["auto_proposals"] and (
                (source == "gmail" and direction == "incoming" and metadata.get("eligible") is True)
                or source == "error"
            ):
                # No unbounded LLM work for each repeated error.
                recent = db.execute(
                    "SELECT 1 FROM jobs j JOIN events e ON e.id=j.event_id WHERE e.event_type=? AND e.content=? AND e.id<>? AND e.timestamp>=datetime('now',?)",
                    (source, content, eid, f"-{settings['incident_cooldown_seconds']} seconds"),
                ).fetchone()
                if not recent:
                    db.execute(
                        "INSERT OR IGNORE INTO jobs(kind,event_id) VALUES(?,?)",
                        ("reply" if source == "gmail" else "incident", eid),
                    )
        return {"id": eid, "duplicate": False}

    def invalidate_proposals(self, eid, db):
        for row in db.execute(
            "SELECT id,source_refs FROM proposals WHERE status IN ('proposed','approved')"
        ).fetchall():
            if eid in json.loads(row["source_refs"]):
                db.execute("UPDATE proposals SET status='expired' WHERE id=?", (row["id"],))

    def events(self, limit=50, offset=0, source="", app="", conversation="", account=None):
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        conditions = []
        args = []
        for column, value in [
            ("event_type", source),
            ("app", app),
            ("conversation_id", conversation),
        ]:
            if value:
                conditions.append(column + "=?")
                args.append(value)
        if account is not None:
            conditions.append("account_id=?")
            args.append(account)
        clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) FROM events" + clause, args).fetchone()[0]
            rows = self.db.execute(
                "SELECT * FROM events" + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
                args + [limit, offset],
            ).fetchall()
        return {"events": [self.public_event(r) for r in rows], "total": total}

    @staticmethod
    def public_event(r):
        d = dict(r)
        d.pop("embedding", None)
        d["metadata"] = json.loads(d.get("metadata") or "{}")
        d["timestamp"] = d["timestamp"] + "Z" if d.get("schema_version") == 2 else d["timestamp"]
        return d

    def get_event(self, eid):
        with self.lock:
            r = self.db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
            return self.public_event(r) if r else None

    def delete(self, ids=None, older_than=None):
        with self.transaction() as db:
            if ids is not None:
                if (
                    not isinstance(ids, list)
                    or len(ids) > 500
                    or any(type(i) is not int for i in ids)
                ):
                    raise ValueError("Invalid event IDs")
                clause = "id IN (" + ",".join("?" for _ in ids) + ")"
                args = ids
            elif older_than:
                clause = "timestamp < ?"
                args = [older_than]
            else:
                clause = "1=1"
                args = []
            victims = {r[0] for r in db.execute("SELECT id FROM events WHERE " + clause, args)}
            for p in db.execute("SELECT id,source_refs FROM proposals").fetchall():
                if victims.intersection(json.loads(p["source_refs"])):
                    db.execute("DELETE FROM proposals WHERE id=?", (p["id"],))
            for r in db.execute(
                "SELECT event_type,account_id,source_event_id FROM events WHERE " + clause, args
            ).fetchall():
                if r[2]:
                    key = hashlib.sha256(
                        (r[0] + "\0" + (r[1] or "") + "\0" + r[2]).encode()
                    ).hexdigest()
                    db.execute("INSERT OR IGNORE INTO event_tombstones VALUES(?)", (key,))
            db.execute("DELETE FROM events WHERE " + clause, args)
            db.execute(
                "INSERT INTO audit_events(action,timestamp,outcome) VALUES('delete',?,?)",
                (utcnow(), str(len(victims))),
            )
        with self.lock:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return len(victims)

    def migrate_legacy(self, timezone):
        tz = ZoneInfo(timezone)
        with self.transaction() as db:
            rows = db.execute(
                "SELECT id,timestamp,content FROM events WHERE schema_version=1"
            ).fetchall()
            for r in rows:
                t = (
                    dt.datetime.fromisoformat(r["timestamp"])
                    .replace(tzinfo=tz)
                    .astimezone(dt.timezone.utc)
                    .strftime("%Y-%m-%d %H:%M:%S")
                )
                db.execute(
                    "UPDATE events SET timestamp=?,content=?,schema_version=2 WHERE id=?",
                    (t, redact(r["content"]), r["id"]),
                )
        return len(rows)

    def stats(self):
        settings = self.settings()
        now = dt.datetime.now(ZoneInfo(settings["timezone"]))
        start = (
            now.replace(hour=0, minute=0, second=0, microsecond=0)
            .astimezone(dt.timezone.utc)
            .strftime("%Y-%m-%d %H:%M:%S")
        )
        with self.lock:
            db = self.db

            def counts(column):
                return [
                    dict(label=r[0], count=r[1])
                    for r in db.execute(
                        f"SELECT {column},count(*) FROM events GROUP BY {column} ORDER BY count(*) DESC"
                    )
                ]

            hours = [{"hour": i, "count": 0} for i in range(24)]
            days = {}
            for r in db.execute(
                "SELECT timestamp FROM events WHERE timestamp>=datetime('now','-30 days')"
            ):
                try:
                    t = (
                        dt.datetime.fromisoformat(r[0])
                        .replace(tzinfo=dt.timezone.utc)
                        .astimezone(ZoneInfo(settings["timezone"]))
                    )
                except ValueError:
                    continue
                hours[t.hour]["count"] += 1
                d = t.date().isoformat()
                days[d] = days.get(d, 0) + 1
            return {
                "total": db.execute("SELECT count(*) FROM events").fetchone()[0],
                "today": db.execute(
                    "SELECT count(*) FROM events WHERE timestamp>=?", (start,)
                ).fetchone()[0],
                "this_week": db.execute(
                    "SELECT count(*) FROM events WHERE timestamp>=datetime('now','-7 days')"
                ).fetchone()[0],
                "unique_apps": db.execute("SELECT count(DISTINCT app) FROM events").fetchone()[0],
                "by_type": counts("event_type"),
                "top_apps": counts("app"),
                "by_hour": hours,
                "by_day": [{"date": d, "count": n} for d, n in sorted(days.items())],
                "embedding_pending": db.execute(
                    "SELECT count(*) FROM events WHERE embedding_status<>'ready'"
                ).fetchone()[0],
                "legacy_events": db.execute(
                    "SELECT count(*) FROM events WHERE schema_version=1"
                ).fetchone()[0],
                "jobs": [
                    dict(r)
                    for r in db.execute("SELECT status,count(*) AS count FROM jobs GROUP BY status")
                ],
                "connectors": [dict(r) for r in db.execute("SELECT * FROM connector_cursors")],
                "usage": [
                    dict(r)
                    for r in db.execute(
                        "SELECT day,kind,count(*) AS calls,sum(input_tokens) AS input_tokens,sum(output_tokens) AS output_tokens FROM usage GROUP BY day,kind ORDER BY day DESC LIMIT 30"
                    )
                ],
            }

    def recover_exports(self):
        # Single service instance: an interrupted create must reconcile, never blindly repeat.
        with self.transaction() as db:
            db.execute("UPDATE proposals SET export_state='unknown' WHERE export_state='exporting'")

    def schedule_incidents(self):
        settings = self.settings()
        if "error" not in settings["cloud_sources"] or "error" in settings["excluded_sources"]:
            return
        with self.transaction() as db:
            row = db.execute(
                "SELECT value FROM migration_state WHERE key='incident_watermark'"
            ).fetchone()
            if not row:
                high = db.execute("SELECT coalesce(max(id),0) FROM events").fetchone()[0]
                db.execute(
                    "INSERT INTO migration_state VALUES('incident_watermark',?)", (str(high),)
                )
                return  # Enabling automation never generates a bill for old history.
            events = db.execute(
                "SELECT id,event_type,content FROM events WHERE id>? ORDER BY id LIMIT 200",
                (int(row[0]),),
            ).fetchall()
            for event in events:
                if event["event_type"] == "error":
                    recent = db.execute(
                        "SELECT 1 FROM jobs j JOIN events e ON e.id=j.event_id WHERE e.event_type='error' AND e.content=? AND e.timestamp>=datetime('now',?)",
                        (event["content"], f"-{settings['incident_cooldown_seconds']} seconds"),
                    ).fetchone()
                    if not recent:
                        db.execute(
                            "INSERT OR IGNORE INTO jobs(kind,event_id) VALUES('incident',?)",
                            (event["id"],),
                        )
            if events:
                db.execute(
                    "UPDATE migration_state SET value=? WHERE key='incident_watermark'",
                    (str(events[-1]["id"]),),
                )

    def context_hash(self, ids):
        with self.lock:
            parts = []
            for eid in ids:
                row = self.db.execute("SELECT id,content FROM events WHERE id=?", (eid,)).fetchone()
                if row is None:
                    raise ValueError("Source event was deleted")
                parts.append(tuple(row))
        return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def unpack_embedding(blob):
    if not blob:
        return None
    if isinstance(blob, str):
        return json.loads(blob)
    if len(blob) == 1536:
        return list(struct.unpack("<384f", blob))
    try:
        return json.loads(blob)
    except (ValueError, UnicodeError):
        return None
