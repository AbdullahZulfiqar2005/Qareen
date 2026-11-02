import json
import os
import re
import secrets
from pathlib import Path

SOURCES = {
    "window",
    "command",
    "history",
    "tab",
    "error",
    "notification",
    "file",
    "keybind",
    "query",
    "browse",
    "chatgpt",
    "claude",
    "gemini",
    "grok",
    "groq",
    "whatsapp",
    "gmail",
    "google-meet",
    "linkedin",
    "instagram",
    "reddit",
    "github",
    "chess",
    "system",
}
ACTIONS = {
    "observed",
    "view",
    "received",
    "sent",
    "send_attempt",
    "draft",
    "response",
    "error",
    "command",
    "resolved",
    "selected",
    "caption",
}
DEFAULT_SETTINGS = {
    "paused": False,
    "excluded_sources": [],
    "cloud_sources": [],
    "retention_days": 90,
    "daily_call_limit": 20,
    "max_context_chars": 18000,
    "auto_proposals": False,
    "gmail_enabled": False,
    "gmail_label": "INBOX",
    "gmail_poll_seconds": 120,
    "timezone": "UTC",
    "preferences": {},
    "incident_cooldown_seconds": 1800,
}


def data_dir():
    p = Path(os.getenv("QAREEN_DATA_DIR", str(Path.home() / ".local/share/qareen"))).expanduser()
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    p.chmod(0o700)
    return p


def db_path():
    return Path(os.getenv("QAREEN_DB_PATH", str(Path.home() / ".qareen.db"))).expanduser()


def token(role="admin"):
    if role not in {"admin", "ingest"}:
        raise ValueError("invalid capability")
    p = data_dir() / (role + ".token")
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        if p.is_symlink():
            raise RuntimeError("Token file must not be a symlink")
    else:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32))
    p.chmod(0o600)
    value = p.read_text().strip()
    if len(value) < 32 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise RuntimeError("Invalid token file; stop the service, remove the file, then restart")
    return value


def redact(text):
    text = str(text or "")
    # Preserve useful context but redact common credentials before any persistence.
    patterns = [
        (r"(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)\S+", r"\1[REDACTED]"),
        (
            r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|otp|verification[_ ]code)\s*[=:]\s*[\"\x27]?)[^\s&\"\x27]+",
            r"\1[REDACTED]",
        ),
        (r"(?i)([?&](?:token|key|code|auth|password|secret)=)[^&#\s]+", r"\1[REDACTED]"),
        (r"\b(?:gsk_|sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}\b", "[REDACTED]"),
        (
            r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----",
            "[REDACTED PRIVATE KEY]",
        ),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def validate_settings(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULT_SETTINGS):
        raise ValueError("Unknown settings")
    out = dict(values)
    for key in ("paused", "auto_proposals", "gmail_enabled"):
        if key in out and type(out[key]) is not bool:
            raise ValueError(key + " must be boolean")
    for key in ("excluded_sources", "cloud_sources"):
        if key in out and (
            not isinstance(out[key], list) or any(x not in SOURCES for x in out[key])
        ):
            raise ValueError("Invalid sources")
    for key, low, high in [
        ("retention_days", 1, 3650),
        ("daily_call_limit", 0, 1000),
        ("max_context_chars", 1000, 40000),
        ("gmail_poll_seconds", 60, 86400),
        ("incident_cooldown_seconds", 60, 86400),
    ]:
        if key in out and (type(out[key]) is not int or not low <= out[key] <= high):
            raise ValueError("Invalid " + key)
    if "preferences" in out:
        if not isinstance(out["preferences"], dict) or len(json.dumps(out["preferences"])) > 5000:
            raise ValueError("Preferences must be a small object")
        out["preferences"] = json.loads(redact(json.dumps(out["preferences"])))
    if "timezone" in out:
        from zoneinfo import ZoneInfo

        ZoneInfo(out["timezone"])
    if "gmail_label" in out and (
        not isinstance(out["gmail_label"], str) or len(out["gmail_label"]) > 100
    ):
        raise ValueError("Invalid Gmail label")
    return out
