"""
Tiny dependency-free .env loader shared by Qareen's Python components
(chat.py, hyprland_monitor.py, the rofi UI backend).

Mirrors the lookup order used by main.go's loadDotEnv() so behavior is
consistent whether GROQ_API_KEY ends up being read by the Go binary or one
of these scripts: $QAREEN_ENV_FILE, then ~/.config/qareen/.env, then
~/.qareen.env, then a .env file in the current working directory. The first
file found wins. Real environment variables already set always take
priority over anything in the file.
"""

import os


def load_dotenv():
    candidates = []

    env_override = os.environ.get("QAREEN_ENV_FILE")
    if env_override:
        candidates.append(env_override)

    home = os.path.expanduser("~")
    candidates.append(os.path.join(home, ".config", "qareen", ".env"))
    candidates.append(os.path.join(home, ".qareen.env"))
    candidates.append(".env")

    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[len("export "):]
                    if "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and val and key not in os.environ:
                        os.environ[key] = val
        except OSError:
            continue
        # Only the first .env file found is used.
        break
