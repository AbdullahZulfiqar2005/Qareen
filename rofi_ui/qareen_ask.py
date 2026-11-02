#!/usr/bin/env python3
"""Rofi wrapper around the authenticated Qareen CLI. Preserves service error status.
No cloud key is needed for local evidence retrieval. Questions are not auto-logged.
"""

import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qareen_dotenv import load_dotenv

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
GUIDANCE_MARKER = "Generating guidance from your digital twin"
QUERY_TIMEOUT_SECS = 90


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def find_qareen_binary() -> str | None:
    # Prefer the local repository binary first (always preferred during development/updates)
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_bin = os.path.join(repo_root, "qareen")
    if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
        return local_bin

    # Fall back to PATH
    on_path = shutil.which("qareen")
    if on_path:
        return on_path
    return None


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Please enter a prompt.")
        return 1

    prompt = sys.argv[1].strip()
    load_dotenv()

    binary = find_qareen_binary()
    if not binary:
        print(
            "Couldn't find the `qareen` binary on $PATH.\n"
            "Build it with `go build -o qareen .` and move it to /usr/local/bin,\n"
            "or run `qareen start` first if it's already installed."
        )
        return 2

    env = os.environ.copy()

    try:
        result = subprocess.run(
            [binary, "query", prompt],
            capture_output=True,
            text=True,
            timeout=QUERY_TIMEOUT_SECS,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print("Qareen took too long to respond (timed out). Try again, or check `qareen status`.")
        return 4
    except OSError as e:
        print(f"Failed to run qareen: {e}")
        return 4

    output = strip_ansi(result.stdout or "")
    stderr = strip_ansi(result.stderr or "")

    if result.returncode != 0:
        print(stderr.strip() or "qareen query failed with no output.")
        return 4

    box_header = "┌──────────────────────────────────────────────────────────┐"
    parts = output.split(box_header)

    if len(parts) >= 3:
        timeline_part = (box_header + parts[1]).strip()
        guidance_part = (box_header + parts[2]).strip()

        # Clean status lines from timeline
        timeline_lines = timeline_part.splitlines()
        clean_timeline = []
        for line in timeline_lines:
            if "Generating guidance" in line:
                continue
            clean_timeline.append(line)
        timeline_part = "\n".join(clean_timeline).strip()

        # Clean trailing logging line from guidance
        guidance_lines = guidance_part.splitlines()
        clean_guidance = []
        for line in guidance_lines:
            if line.strip().startswith("[INFO]") and "Logging Event" in line:
                continue
            clean_guidance.append(line)
        guidance_part = "\n".join(clean_guidance).strip()

        answer = guidance_part + "\n\n" + timeline_part
    else:
        # Fallback to standard line-by-line filtering if the format doesn't match
        lines = output.splitlines()
        cleaned_lines = []
        for line in lines:
            line_str = line.strip()
            if line_str.startswith("[INFO]") and "Logging Event" in line_str:
                continue
            if "Qareen: Scanning" in line_str or "Timeline Context Retrieved" in line_str:
                continue
            if "Generating guidance" in line_str or line_str.startswith("==="):
                continue
            cleaned_lines.append(line)
        answer = "\n".join(cleaned_lines).strip()

    if not answer:
        answer = "No answer was generated. Is `qareen start` running, and does your history have anything relevant yet?"

    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
