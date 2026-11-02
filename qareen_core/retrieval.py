import datetime as dt
import hashlib
import heapq
import math
import os
import re
import struct
import threading
from zoneinfo import ZoneInfo

from .config import SOURCES
from .store import unpack_embedding


class Embeddings:
    """One warm, serialized embedding model; demo mode is explicitly non-semantic."""

    def __init__(self, demo=False):
        self.demo = demo
        self.model = None
        self.lock = threading.Lock()
        self.version = "demo-hash-v1" if demo else "all-MiniLM-L6-v2:normalized:v1"

    def encode(self, texts):
        if len(texts) > 64 or any(not isinstance(t, str) or len(t) > 100000 for t in texts):
            raise ValueError("Embedding batch too large")
        with self.lock:
            if self.demo:
                result = []
                for text in texts:
                    v = [0.0] * 384
                    for word in re.findall(r"\w+", text.lower()):
                        h = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "little")
                        v[h % 384] += 1
                    n = math.sqrt(sum(x * x for x in v)) or 1
                    result.append([x / n for x in v])
                return result
            if self.model is None:
                os.environ.setdefault("OMP_NUM_THREADS", "1")
                from sentence_transformers import SentenceTransformer

                self.model = SentenceTransformer(
                    "sentence-transformers/all-MiniLM-L6-v2", device="cpu"
                )
            return self.model.encode(
                texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False
            ).tolist()


def chunks(text):
    # Conservative overlapping word windows; retain original events for evidence.
    words = text.split()
    return [" ".join(words[i : i + 160]) for i in range(0, len(words), 130)] or [text]


def initialize_chunks(store):
    with store.transaction() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS event_chunks(event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,chunk_index INTEGER,content TEXT,embedding BLOB,version TEXT,PRIMARY KEY(event_id,chunk_index))"
        )
        db.execute(
            "UPDATE events SET embedding_status='pending' WHERE embedding_status='ready' AND NOT EXISTS(SELECT 1 FROM event_chunks c WHERE c.event_id=events.id)"
        )


def enrich_once(store, model, limit=16):
    with store.lock:
        rows = store.db.execute(
            "SELECT id,content FROM events WHERE (embedding_status<>'ready' OR embedding_version<>?) AND (next_embedding_at IS NULL OR next_embedding_at<=datetime('now')) ORDER BY id LIMIT ?",
            (model.version, limit),
        ).fetchall()
    for row in rows:
        try:
            parts = chunks(row["content"])
            vectors = []
            for i in range(0, len(parts), 32):
                vectors.extend(model.encode(parts[i : i + 32]))
            with store.transaction() as db:
                current = db.execute(
                    "SELECT content FROM events WHERE id=?", (row["id"],)
                ).fetchone()
                if not current or current[0] != row["content"]:
                    continue
                db.execute("DELETE FROM event_chunks WHERE event_id=?", (row["id"],))
                db.executemany(
                    "INSERT INTO event_chunks VALUES(?,?,?,?,?)",
                    [
                        (row["id"], i, p, struct.pack("<384f", *v), model.version)
                        for i, (p, v) in enumerate(zip(parts, vectors))
                    ],
                )
                db.execute(
                    "UPDATE events SET embedding=?,embedding_version=?,embedding_status='ready',next_embedding_at=NULL WHERE id=?",
                    (struct.pack("<384f", *vectors[0]), model.version, row["id"]),
                )
        except Exception:
            with store.transaction() as db:
                db.execute(
                    "UPDATE events SET embedding_status='retry',embedding_attempts=embedding_attempts+1,next_embedding_at=datetime('now','+60 seconds') WHERE id=?",
                    (row["id"],),
                )
            return False
    return bool(rows)


def time_bounds(query, timezone="UTC", now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    now = now.astimezone(ZoneInfo(timezone))
    q = query.lower()
    start = end = None
    if "yesterday" in q:
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - dt.timedelta(days=1)
    elif (
        "today" in q
        or "summarize my day" in q
        or re.search(r"this (morning|afternoon|evening|night)", q)
    ):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + dt.timedelta(days=1)
    else:
        m = re.search(r"last\s+(?:(\d+)\s+)?(hours?|minutes?|mins?|days?)\b", q)
        if m:
            n = int(m[1] or 1)
            if not 1 <= n <= 365:
                raise ValueError("Time interval out of range")
            unit = m[2]
            delta = dt.timedelta(
                **{
                    "hours"
                    if unit.startswith("hour")
                    else "minutes"
                    if unit.startswith(("min"))
                    else "days": n
                }
            )
            start = now - delta
            end = now

    if start and end and (end - start) >= dt.timedelta(hours=23):
        for word, lo, hi in [
            ("morning", 6, 12),
            ("afternoon", 12, 18),
            ("evening", 18, 22),
            ("night", 22, 24),
        ]:
            if re.search(r"\b" + word + r"\b", q):
                day = start.replace(hour=0, minute=0, second=0, microsecond=0)
                start = day + dt.timedelta(hours=lo)
                end = day + dt.timedelta(hours=hi)
                break

    def utc(x):
        return x.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if x else None

    return utc(start), utc(end)


STOP = {
    "this",
    "morning",
    "afternoon",
    "evening",
    "night",
    "what",
    "did",
    "i",
    "do",
    "my",
    "the",
    "a",
    "an",
    "to",
    "of",
    "in",
    "on",
    "was",
    "were",
    "how",
    "when",
    "is",
    "it",
    "me",
    "please",
    "summarize",
    "summary",
    "activity",
    "activities",
    "say",
    "said",
    "show",
    "find",
    "last",
    "today",
    "yesterday",
    "hour",
    "hours",
    "minutes",
    "mins",
    "day",
    "days",
    "from",
    "with",
    "about",
}


def search(store, model, query, k=12, source="", app="", start=None, end=None):
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("Query must contain 1–2000 characters")
    k = max(1, min(int(k), 200))
    start0, end0 = time_bounds(query, store.settings()["timezone"])
    start = start or start0
    end = end or end0
    if source and source not in SOURCES:
        raise ValueError("Unknown source")
    if not source:
        source = next(
            (s for s in SOURCES if re.search(r"\b" + re.escape(s) + r"\b", query.lower())), ""
        )
    where = []
    args = []
    for cond, val in [
        ("e.timestamp>=?", start),
        ("e.timestamp<?", end),
        ("e.event_type=?", source),
        ("e.app=?", app),
    ]:
        if val:
            where.append(cond)
            args.append(val)
    terms = [
        w
        for w in re.findall(r"[\w]+", query.lower())
        if w not in STOP and not w.isdigit() and w != source
    ][:20]
    clause = " AND ".join(where) or "1=1"
    fts = " OR ".join('"' + w + '"' for w in terms)
    qvec = None
    if not (start and not terms):
        try:
            qvec = model.encode([query])[0]
        except Exception:
            pass
    with store.lock:
        total = store.db.execute("SELECT count(*) FROM events e WHERE " + clause, args).fetchone()[
            0
        ]
        if start and not terms:
            # Deterministic evenly-spaced coverage of the entire interval, not latest N.
            stride = max(1, math.ceil(total / k))
            rows = store.db.execute(
                "SELECT * FROM (SELECT e.*,ROW_NUMBER() OVER(ORDER BY timestamp,id) AS rn FROM events e WHERE "
                + clause
                + ") WHERE (rn-1)%?=0 ORDER BY rn LIMIT ?",
                args + [stride, k],
            ).fetchall()
            return {
                "results": [dict(store.public_event(r), score=None) for r in rows],
                "coverage": {
                    "matched": total,
                    "returned": len(rows),
                    "omitted": total - len(rows),
                    "mode": "chronological sample" if total > k else "chronological",
                    "start": start,
                    "end": end,
                },
            }
        lexical = []
        if fts:
            lexical = store.db.execute(
                "SELECT e.* FROM events_fts JOIN events e ON e.id=events_fts.rowid WHERE events_fts MATCH ? AND "
                + clause
                + " ORDER BY bm25(events_fts) LIMIT 200",
                [fts] + args,
            ).fetchall()
        # Bound CPU on large histories; coverage explicitly discloses the candidate cap.
        candidate_rows = store.db.execute(
            "SELECT e.* FROM events e WHERE " + clause + " ORDER BY id DESC LIMIT 5000", args
        ).fetchall()
        candidates = {r["id"]: r for r in candidate_rows}
        candidates.update({r["id"]: r for r in lexical})
        scores = {r["id"]: 1 / (40 + i) for i, r in enumerate(lexical)}
        mode = "keyword"
        try:
            if qvec is None:
                raise ValueError("Embedding model unavailable")
            vectors = store.db.execute(
                "SELECT c.event_id,c.embedding FROM event_chunks c JOIN events e ON e.id=c.event_id WHERE c.version=? AND "
                + clause
                + " AND e.id IN (SELECT e.id FROM events e WHERE "
                + clause
                + " ORDER BY e.id DESC LIMIT 5000)",
                [model.version] + args + args,
            ).fetchall()
            best = {}
            for eid, blob in vectors:
                v = unpack_embedding(blob)
                if v and len(v) == len(qvec):
                    score = sum(a * b for a, b in zip(v, qvec))
                    if score >= 0.15:
                        best[eid] = max(best.get(eid, -1), score)
            for rank, (eid, _) in enumerate(heapq.nlargest(200, best.items(), key=lambda x: x[1])):
                scores[eid] = scores.get(eid, 0) + 1 / (40 + rank)
            mode = "hybrid" if lexical else "semantic"
        except Exception:
            pass  # Full text and chronological retrieval remain available offline.
        ordered = sorted(scores, key=scores.get, reverse=True)[:k]
        results = [
            dict(store.public_event(candidates[eid]), score=round(scores[eid], 6))
            for eid in ordered
            if eid in candidates
        ]
    return {
        "results": results,
        "coverage": {
            "matched": total,
            "returned": len(results),
            "candidates": len(candidates),
            "candidate_limit": 5000,
            "mode": mode,
            "start": start,
            "end": end,
        },
        "score_kind": "reciprocal rank relevance; not probability",
    }
