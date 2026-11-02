"""Opt-in Hyprland log monitor; records incidents, never executes remediation."""

import argparse
import hashlib
import os
import time
from pathlib import Path

from qareen_client import call


def find_log():
    runtime = Path(os.getenv("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "hypr"
    candidates = list(runtime.glob("*/hyprland.log"))
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def follow(path, stop=None):
    """Flush short incidents on idle, and reopen rotated/truncated logs."""
    identity = None
    offset = 0
    pending = []
    last_line = time.monotonic()
    while stop is None or not stop.is_set():
        current = path or find_log()
        if not current or not current.exists():
            time.sleep(1)
            continue
        stat = current.stat()
        key = (str(current), stat.st_ino)
        if key != identity or stat.st_size < offset:
            identity = key
            offset = 0
        with current.open(errors="replace") as stream:
            stream.seek(offset)
            for _ in range(200):
                before = stream.tell()
                line = stream.readline(100001)
                if not line or not line.endswith("\n"):
                    stream.seek(before)
                    break
                offset = stream.tell()
                if any(word in line.lower() for word in ("error", "crash", "failed", "fatal")):
                    pending.append(line[:4000])
                    last_line = time.monotonic()
            if pending and (len(pending) >= 25 or time.monotonic() - last_line >= 2):
                text = "".join(pending[:25])
                event_id = hashlib.sha256((str(identity) + str(offset) + text).encode()).hexdigest()
                try:
                    call(
                        "/ingest",
                        {
                            "event_type": "error",
                            "app": "Hyprland",
                            "action": "error",
                            "source_event_id": event_id,
                            "content": text,
                        },
                    )
                    del pending[:25]
                except Exception as error:
                    print("Incident delivery pending:", type(error).__name__)
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, help="Explicit live log or crash log to inspect")
    args = parser.parse_args()
    try:
        follow(args.log)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
