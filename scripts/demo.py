"""Create synthetic, isolated demo data; never scans this machine or contacts a provider."""

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qareen_core.retrieval import Embeddings, enrich_once, initialize_chunks
from qareen_core.store import Store
from qareen_core.workflows import Proposals


class DemoProvider:
    def generate(self, task, evidence, examples=None, question=""):
        return {
            "content": "Demo proposal: Thanks for the update. I will review the notes and reply with any questions.",
            "evidence_ids": [evidence[0]["id"]],
            "abstain": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    args = parser.parse_args()
    store = Store(args.db)
    initialize_chunks(store)
    now = dt.datetime.now(dt.timezone.utc)
    records = [
        ("command", "terminal", "Observed shell history: go test ./... (exit status unknown)"),
        ("file", "editor", "Edited the SQLite cursor migration in collector_storage.go"),
        (
            "error",
            "example.service",
            "Synthetic incident: database is locked while processing a batch",
        ),
        ("chatgpt", "Project notes", "Compared lexical retrieval and normalized vector search"),
        ("whatsapp", "demo-contact", "Can you review the project notes when you have time?"),
    ]
    for i, (source, app, content) in enumerate(records):
        result = store.ingest(
            {
                "event_type": source,
                "source_event_id": f"demo-{i}",
                "app": app,
                "actor_id": "demo-contact",
                "conversation_id": "demo-thread" if source == "whatsapp" else "",
                "direction": "incoming" if source == "whatsapp" else "unknown",
                "content": content,
                "occurred_at": (now - dt.timedelta(hours=5 - i)).isoformat(),
                "metadata": {"synthetic": True},
            }
        )
        if source == "whatsapp" and not result.get("duplicate"):
            Proposals(store, DemoProvider()).create(result["id"])
    while enrich_once(store, Embeddings(demo=True)):
        pass
    store.close()
    print(
        "Synthetic demo ready. Run the service with QAREEN_DB_PATH pointing to this database and --demo."
    )


if __name__ == "__main__":
    main()
