#!/usr/bin/env python3
"""
Backend for the Qareen rofi UI.

Takes a single prompt as argv[1], shells out to the existing `qareen query`
CLI (reusing its tested embedding-similarity + Arch Wiki + Groq pipeline
instead of re-implementing it here), strips the ANSI colour codes and
timeline preamble it prints, and prints just the final answer to stdout.

Reusing `qareen query` also means every question you ask through the rofi
UI is automatically logged back into ~/.qareen.db as a memory event (the Go
binary already does this) - so the digital twin keeps getting richer every
time you use it, without this script needing to duplicate that logic.

Exit codes:
  0  success, answer printed to stdout
  1  usage error
  2  qareen binary not found / daemon not reachable
  3  GROQ_API_KEY missing
  4  qareen query itself failed / timed out
"""
import os
import re
import shutil
import subprocess
import sys
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qareen_dotenv import load_dotenv  # noqa: E402

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
GUIDANCE_MARKER = "Generating guidance from your digital twin"
QUERY_TIMEOUT_SECS = 90


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def find_qareen_binary() -> Optional[str]:
    # Prefer whatever's on $PATH (matches README's `sudo mv qareen
    # /usr/local/bin/` install step); fall back to a binary sitting next to
    # this repo in case the user hasn't installed it system-wide yet.
    on_path = shutil.which("qareen")
    if on_path:
        return on_path
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_bin = os.path.join(repo_root, "qareen")
    if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
        return local_bin
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
    if not env.get("GROQ_API_KEY"):
        print(
            "GROQ_API_KEY is not set.\n"
            "Put it in ~/.config/qareen/.env as:\n"
            '  GROQ_API_KEY="your-key-here"\n'
            "Get a free key at https://console.groq.com/keys"
        )
        return 3

    try:
        result = subprocess.run(
            [binary, "query", prompt],
            capture_output=True,
            text=True,
            timeout=QUERY_TIMEOUT_SECS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        print("Qareen took too long to respond (timed out). Try again, or check `qareen status`.")
        return 4
    except OSError as e:
        print(f"Failed to run qareen: {e}")
        return 4

    output = strip_ansi(result.stdout or "")
    stderr = strip_ansi(result.stderr or "")

    if result.returncode != 0 and not output.strip():
        print(stderr.strip() or "qareen query failed with no output.")
        return 4

    marker_idx = output.find(GUIDANCE_MARKER)
    if marker_idx != -1:
        after_marker = output[marker_idx + len(GUIDANCE_MARKER):]
        # The marker line ends in "..." followed by blank lines before the
        # actual streamed answer starts - strip those off.
        answer = after_marker.lstrip(".\n \t").strip()
    else:
        answer = output.strip()

    if not answer:
        answer = "No answer was generated. Is `qareen start` running, and does your history have anything relevant yet?"

    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
