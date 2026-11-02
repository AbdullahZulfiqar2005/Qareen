"""Interactive evidence-based chat. No shell or message execution tools exist."""

import argparse
import json
import os
import sys
from pathlib import Path

from qareen_client import call
from qareen_core.config import data_dir, redact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="*")
    parser.add_argument("--session", default="default")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--documentation", action="store_true")
    args = parser.parse_args()
    if not args.session.replace("-", "").replace("_", "").isalnum():
        parser.error("Session must contain letters, numbers, hyphens or underscores")
    path = data_dir() / ("chat-" + args.session + ".json")
    history = json.loads(path.read_text()) if path.exists() and not args.reset else []
    if args.reset:
        path.unlink(missing_ok=True)
    while True:
        try:
            question = (
                " ".join(args.question) if args.question else input("You (quit to exit): ").strip()
            )
        except (EOFError, KeyboardInterrupt):
            break
        if question in {"quit", "exit"}:
            break
        if not question:
            continue
        # Previous questions help resolve follow-ups, without treating model text as evidence.
        context = "\n".join("Previous question: " + item["question"] for item in history[-2:])
        query = (context + "\nCurrent question: " + question) if context else question
        try:
            result = call("/api/ask", {"query": query[-2000:], "documentation": args.documentation})
            print(result["answer"])
            for event in result["results"]:
                print(
                    f"[event:{event['id']}] {event['timestamp']} {event['event_type']}: {event['content'][:500]}"
                )
            for document in result.get("documentation", {}).get("results", []):
                print(f"Documentation: {document['source']}\n{document['content']}")
            print("Coverage:", json.dumps(result["coverage"]))
            history.append({"question": redact(question)})
            temporary = Path(str(path) + ".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(history[-10:], stream)
            temporary.replace(path)
        except (RuntimeError, OSError) as error:
            print(str(error), file=sys.stderr)
            if args.question:
                return 1
        if args.question:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
