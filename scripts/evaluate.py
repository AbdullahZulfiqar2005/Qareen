"""Reproducible synthetic retrieval checks and local storage timings; no cloud calls."""

import argparse
import json
import platform
import resource
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qareen_core.retrieval import Embeddings, enrich_once, initialize_chunks, search
from qareen_core.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=10000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 100 <= args.events <= 100000:
        parser.error("events must be 100–100000")
    with tempfile.TemporaryDirectory(prefix="qareen-evaluation-") as directory:
        store = Store(Path(directory) / "events.db")
        initialize_chunks(store)
        model = Embeddings(demo=True)
        targets = {}
        started = time.perf_counter()
        for i in range(args.events):
            key = f"uniquetopic{i}" if i % 100 == 0 else "routine activity"
            result = store.ingest(
                {
                    "event_type": "command",
                    "source_event_id": str(i),
                    "content": f"{key}: inspected a synthetic development task",
                }
            )
            if i % 100 == 0:
                targets[key] = result["id"]
        ingest = time.perf_counter() - started
        # Bound benchmark enrichment work to keep the default reproducible on a laptop.
        started = time.perf_counter()
        for _ in range(min(64, (args.events + 15) // 16)):
            enrich_once(store, model)
        enrichment = time.perf_counter() - started
        latencies = []
        hits = 0
        for key, eid in list(targets.items())[:20]:
            started = time.perf_counter()
            result = search(store, model, key, k=5)
            latencies.append((time.perf_counter() - started) * 1000)
            hits += eid in [e["id"] for e in result["results"]]
        sorted_times = sorted(latencies)
        output = {
            "fixture": "synthetic exact-topic retrieval; hash demo vectors, not an LLM/semantic quality evaluation",
            "python": platform.python_version(),
            "platform": platform.platform(),
            "events": args.events,
            "ingest_seconds": round(ingest, 3),
            "events_per_second": round(args.events / ingest, 1),
            "enrichment_seconds": round(enrichment, 3),
            "embedded_events": args.events - store.stats()["embedding_pending"],
            "retrieval_cases": len(latencies),
            "hit_at_5": hits / len(latencies),
            "retrieval_p50_ms": round(sorted_times[len(latencies) // 2], 2),
            "retrieval_p95_ms": round(
                sorted_times[min(len(latencies) - 1, int(len(latencies) * 0.95))], 2
            ),
            "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
            "sqlite_bytes": store.path.stat().st_size,
        }
        store.close()
    text = json.dumps(output, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
