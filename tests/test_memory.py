import datetime as dt
import json
import sqlite3
import struct
from concurrent.futures import ThreadPoolExecutor

import pytest

from qareen_core.config import redact, token
from qareen_core.retrieval import Embeddings, enrich_once, search, time_bounds
from qareen_core.store import Store, unpack_embedding


def add(store, i=0, **extra):
    return store.ingest(
        dict(
            event_type="chatgpt",
            content=f"notes about SQLite migration {i}",
            source_event_id=str(i),
            **extra,
        )
    )["id"]


def test_fiftieth_event_and_concurrent_ingestion(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: add(store, i), range(80)))
    assert len(set(results)) == 80
    assert store.events()["total"] == 80


def test_identity_deduplication_scopes_account_and_conversation(store):
    first = add(store, account_id="one")
    assert add(store, account_id="one") == first
    assert add(store, account_id="two") != first


def test_revision_invalidates_vector(store):
    eid = add(store)
    model = Embeddings(demo=True)
    assert enrich_once(store, model)
    store.ingest(
        {
            "event_type": "chatgpt",
            "source_event_id": "0",
            "content": "fully streamed final answer",
            "revision_update": True,
        }
    )
    assert store.get_event(eid)["embedding_status"] == "pending"
    assert enrich_once(store, model)
    assert search(store, model, "fully streamed")["results"][0]["id"] == eid


def test_backfill_interleaved_sources_and_restart(store):
    add(store)
    with store.transaction() as db:
        db.execute(
            "INSERT INTO events(timestamp,event_type,content,schema_version) VALUES(datetime('now'),'command','go test migration',2)"
        )
    add(store, 2)
    assert enrich_once(store, Embeddings(demo=True))
    assert store.stats()["embedding_pending"] == 0
    assert not enrich_once(store, Embeddings(demo=True))


def test_embedding_failure_does_not_block_persistence(store):
    class Broken:
        version = "broken"

        def encode(self, texts):
            raise TimeoutError()

    add(store)
    assert not enrich_once(store, Broken())
    add(store, 1)
    assert store.events()["total"] == 2
    assert store.get_event(1)["embedding_status"] == "retry"


def test_deletion_removes_fts_vectors_and_blocks_replay(store):
    eid = add(store)
    enrich_once(store, Embeddings(demo=True))
    assert store.delete([eid]) == 1
    assert not search(store, Embeddings(demo=True), "SQLite")["results"]
    assert store.db.execute("SELECT count(*) FROM event_chunks").fetchone()[0] == 0
    assert store.ingest({"event_type": "chatgpt", "source_event_id": "0", "content": "replayed"})[
        "deleted"
    ]


def test_cloud_defaults_and_pause(store):
    assert store.settings()["cloud_sources"] == []
    store.update_settings({"paused": True})
    assert store.ingest({"event_type": "error", "content": "private"}) == {"ignored": True}
    store.update_settings({"paused": False, "excluded_sources": ["chatgpt"]})
    assert store.ingest({"event_type": "chatgpt", "content": "private"}) == {"ignored": True}


@pytest.mark.parametrize(
    "value",
    [{"daily_call_limit": -1}, {"paused": "false"}, {"cloud_sources": ["bad"]}, {"unknown": True}],
)
def test_invalid_settings(store, value):
    with pytest.raises(ValueError):
        store.update_settings(value)


def test_redaction_before_storage(store):
    result = store.ingest(
        {
            "event_type": "command",
            "content": "curl https://host/?token=secret password=hidden Authorization: Bearer abc123",
        }
    )
    content = store.get_event(result["id"])["content"]
    assert "secret" not in content and "hidden" not in content and "abc123" not in content
    assert "PRIVATE KEY" not in redact(
        "-----BEGIN RSA PRIVATE KEY-----\nsecret\n-----END RSA PRIVATE KEY-----"
    ).replace("[REDACTED PRIVATE KEY]", "")


def test_private_modes_and_separate_capabilities(store):
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert token("admin") != token("ingest")
    assert token("admin") == token("admin")


def test_yesterday_timezone_half_open():
    start, end = time_bounds(
        "what happened yesterday",
        "Asia/Karachi",
        dt.datetime(2026, 11, 1, 12, tzinfo=dt.timezone.utc),
    )
    assert (start, end) == ("2026-10-30 19:00:00", "2026-10-31 19:00:00")


def test_temporal_keyword_and_full_day_coverage(store):
    for i in range(60):
        store.ingest(
            {
                "event_type": "command",
                "source_event_id": str(i),
                "content": "migration" if i == 0 else "unrelated",
                "occurred_at": f"2026-10-31T{(i // 3):02}:00:00Z",
            }
        )
    result = search(
        store,
        Embeddings(demo=True),
        "migration",
        start="2026-10-31 00:00:00",
        end="2026-11-01 00:00:00",
    )
    assert result["results"][0]["content"] == "migration"
    result = search(
        store,
        Embeddings(demo=True),
        "summarize my day",
        k=10,
        start="2026-10-31 00:00:00",
        end="2026-11-01 00:00:00",
    )
    assert result["coverage"]["omitted"] == 50
    assert result["results"][0]["id"] == 1
    assert result["results"][-1]["id"] > 50


def test_binary_first_byte_json_collision():
    blob = bytearray(struct.pack("<384f", *([0.1] * 384)))
    blob[0] = ord("[")
    assert len(unpack_embedding(bytes(blob))) == 384
    assert unpack_embedding(json.dumps([1, 2]).encode()) == [1, 2]


def test_legacy_migration_requires_explicit_timezone(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE events(id INTEGER PRIMARY KEY,timestamp TEXT,event_type TEXT,app TEXT,content TEXT,embedding BLOB)"
    )
    db.execute(
        "INSERT INTO events VALUES(1,'2026-10-31 12:00:00','command','shell','password=hidden',NULL)"
    )
    db.commit()
    db.close()
    store = Store(path)
    assert store.get_event(1)["schema_version"] == 1
    assert store.migrate_legacy("Asia/Karachi") == 1
    assert store.get_event(1)["timestamp"] == "2026-10-31 07:00:00Z"
    assert "hidden" not in store.get_event(1)["content"]
    store.close()


def test_morning_bounds():
    start, end = time_bounds(
        "what did I do yesterday morning",
        "UTC",
        dt.datetime(2026, 11, 1, 12, tzinfo=dt.timezone.utc),
    )
    assert (start, end) == ("2026-10-31 06:00:00", "2026-10-31 12:00:00")
