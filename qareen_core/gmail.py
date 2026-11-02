"""Read mail and export explicitly approved drafts. No send/delete API exists here."""

import base64
import datetime as dt
import email.utils
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from html.parser import HTMLParser

from .store import utcnow


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        if tag in {"br", "p", "div"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def body_text(payload):
    texts = []
    html = []

    def visit(part):
        if part.get("filename"):
            return  # Attachments are never downloaded automatically.
        raw = part.get("body", {}).get("data", "")
        if raw:
            decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode(
                "utf-8", errors="replace"
            )
            if part.get("mimeType") == "text/plain":
                texts.append(decoded)
            elif part.get("mimeType") == "text/html":
                p = PlainHTML()
                p.feed(decoded)
                html.append("".join(p.parts))
        for child in part.get("parts", []):
            visit(child)

    visit(payload)
    return "\n".join(texts or html)[:80000]


class Gmail:
    def __init__(self, store, request=None):
        self.store = store
        self.request = request or self._request
        self.sync_lock = threading.Lock()

    def _access_token(self):
        if os.getenv("QAREEN_GMAIL_ACCESS_TOKEN"):
            return os.environ["QAREEN_GMAIL_ACCESS_TOKEN"]
        import keyring

        data = keyring.get_password("qareen", "gmail-oauth")
        if not data:
            raise ValueError("Connect Gmail with gmail_setup.py first")
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        credentials = Credentials.from_authorized_user_info(json.loads(data))
        if not credentials.valid:
            credentials.refresh(Request())
            keyring.set_password("qareen", "gmail-oauth", credentials.to_json())
        return credentials.token

    def _request(self, method, path, body=None):
        # Caller-selected paths are internal constants/quoted provider IDs, never model output.
        req = urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/" + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": "Bearer " + self._access_token(),
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read(4000001)
            if len(raw) > 4000000:
                raise ValueError("Gmail response too large")
            return json.loads(raw)

    def sync(self):
        if not self.sync_lock.acquire(blocking=False):
            raise ValueError("Gmail sync is already running")
        try:
            return self._sync()
        finally:
            self.sync_lock.release()

    def _sync(self):
        settings = self.store.settings()
        if (
            not settings["gmail_enabled"]
            or settings["paused"]
            or "gmail" in settings["excluded_sources"]
        ):
            return 0
        profile = self.request("GET", "profile")
        account = profile["emailAddress"].lower()
        label = settings["gmail_label"]
        cursor_key = "gmail:" + account + ":" + label
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT cursor FROM connector_cursors WHERE connector=?", (cursor_key,)
            ).fetchone()
        previous = row[0] if row else None
        ids = set()
        next_cursor = profile["historyId"]
        page = None
        try:
            if previous:
                while True:
                    params = {
                        "startHistoryId": previous,
                        "historyTypes": "messageAdded",
                        "maxResults": 100,
                    }
                    if page:
                        params["pageToken"] = page
                    data = self.request("GET", "history?" + urllib.parse.urlencode(params))
                    for record in data.get("history", []):
                        for added in record.get("messagesAdded", []):
                            msg = added["message"]
                            if label in msg.get("labelIds", []):
                                ids.add(msg["id"])
                    page = data.get("nextPageToken")
                    next_cursor = data.get("historyId", next_cursor)
                    if len(ids) > 1000:
                        raise ValueError("Large mailbox backlog; narrow label before resync")
                    if not page:
                        break
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            previous = None
        if not previous:
            # Initial sync deliberately bounded and marked historical (no automatic reply flood).
            data = self.request(
                "GET", "messages?" + urllib.parse.urlencode({"labelIds": label, "maxResults": 50})
            )
            ids = {m["id"] for m in data.get("messages", [])}
        threads = set()
        added = 0
        for mid in sorted(ids):
            message = self.request(
                "GET", "messages/" + urllib.parse.quote(mid, safe="") + "?format=metadata"
            )
            threads.add(message["threadId"])
        for thread_id in threads:
            thread = self.request(
                "GET", "threads/" + urllib.parse.quote(thread_id, safe="") + "?format=full"
            )
            messages = thread.get("messages", [])
            snapshot = [m["id"] for m in messages]
            for msg in messages[-30:]:
                if "DRAFT" in msg.get("labelIds", []):
                    continue
                headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
                sender = email.utils.parseaddr(headers.get("from", ""))[1].lower()
                if not sender:
                    continue
                direction = (
                    "outgoing"
                    if sender == account or "SENT" in msg.get("labelIds", [])
                    else "incoming"
                )
                automated = (
                    bool(headers.get("list-id"))
                    or headers.get("auto-submitted", "no").lower() != "no"
                    or headers.get("precedence", "").lower() in {"bulk", "list", "junk"}
                )
                eligible = (
                    bool(previous)
                    and direction == "incoming"
                    and not automated
                    and msg["id"] in ids
                    and label in msg.get("labelIds", [])
                )
                occurred = dt.datetime.fromtimestamp(
                    int(msg["internalDate"]) / 1000, dt.timezone.utc
                ).isoformat()
                content = (
                    "Subject: " + headers.get("subject", "") + "\n" + body_text(msg["payload"])
                )
                result = self.store.ingest(
                    {
                        "event_type": "gmail",
                        "source_event_id": msg["id"],
                        "account_id": account,
                        "conversation_id": thread_id,
                        "actor_id": sender,
                        "app": sender,
                        "direction": direction,
                        "action": "received" if direction == "incoming" else "sent",
                        "occurred_at": occurred,
                        "content": content,
                        "metadata": {
                            "eligible": eligible,
                            "thread_id": thread_id,
                            "message_id": msg["id"],
                            "thread_message_ids": snapshot,
                            "message_id_header": headers.get("message-id", ""),
                            "references": headers.get("references", ""),
                            "subject": headers.get("subject", ""),
                            "automated": automated,
                            "group": len(
                                {
                                    addr.lower()
                                    for _, addr in email.utils.getaddresses(
                                        [headers.get("to", ""), headers.get("cc", "")]
                                    )
                                    if addr.lower() not in {account, sender}
                                }
                            )
                            > 0,
                        },
                    }
                )
                added += int(
                    not result.get("duplicate", False) and not result.get("ignored", False)
                )
        with self.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO connector_cursors VALUES(?,?,?,?)",
                (
                    cursor_key,
                    str(next_cursor),
                    utcnow(),
                    "ok; initial sync limited to 50 messages" if not previous else "ok",
                ),
            )
        return added

    def export_draft(self, proposals, pid, revision):
        with self.store.transaction() as db:
            p = proposals.get(pid)
            if p["revision"] != revision:
                raise ValueError("Proposal changed")
            if p["status"] == "exported" and p["provider_draft_id"]:
                return {"draft_id": p["provider_draft_id"]}
            if p["status"] != "approved" or p["expires_at"] < utcnow():
                raise ValueError("Approve this revision before export")
            if p["context_version"] != self.store.context_hash(p["source_refs"]):
                raise ValueError("Evidence changed")
            if p["export_state"] == "exporting":
                raise ValueError("Export already in progress")
            uncertain = p["export_state"] == "unknown"
            if not uncertain:
                db.execute("UPDATE proposals SET export_state='exporting' WHERE id=?", (pid,))
        marker = f"qareen-{pid}-{revision}@localhost"
        attempted = uncertain
        try:
            if uncertain:
                matches = self.request(
                    "GET",
                    "drafts?"
                    + urllib.parse.urlencode({"q": "rfc822msgid:" + marker, "maxResults": 10}),
                ).get("drafts", [])
                if len(matches) != 1:
                    raise ValueError(
                        "Export outcome uncertain; inspect Gmail drafts manually. No automatic retry."
                    )
                draft = matches[0]
            else:
                evidence = [self.store.get_event(i) for i in p["source_refs"]]
                source = next(
                    (
                        e
                        for e in evidence
                        if e and e["event_type"] == "gmail" and e["actor_id"] in p["recipient_ids"]
                    ),
                    None,
                )
                if not source:
                    raise ValueError("Gmail source not available")
                meta = source["metadata"]
                thread_id = meta["thread_id"]
                current = self.request(
                    "GET", "threads/" + urllib.parse.quote(thread_id, safe="") + "?format=metadata"
                )
                if set(meta["thread_message_ids"]) != {
                    m["id"] for m in current.get("messages", [])
                }:
                    raise ValueError("Thread changed; regenerate and review the reply")
                profile = self.request("GET", "profile")
                if profile["emailAddress"].lower() != source["account_id"]:
                    raise ValueError("Connected account changed")
                mail = EmailMessage()
                mail["To"] = ", ".join(p["recipient_ids"])
                mail["From"] = source["account_id"]
                subject = meta["subject"]
                mail["Subject"] = subject if subject.lower().startswith("re:") else "Re: " + subject
                mail["Message-ID"] = "<" + marker + ">"
                mail["X-Qareen-Proposal"] = pid
                if meta.get("message_id_header"):
                    mail["In-Reply-To"] = meta["message_id_header"]
                    mail["References"] = (
                        meta.get("references", "") + " " + meta["message_id_header"]
                    ).strip()
                mail.set_content(p["content"])
                encoded = base64.urlsafe_b64encode(mail.as_bytes()).decode()
                attempted = True
                draft = self.request(
                    "POST", "drafts", {"message": {"raw": encoded, "threadId": thread_id}}
                )
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE proposals SET provider_draft_id=?,export_state='complete',status='exported' WHERE id=?",
                    (draft["id"], pid),
                )
                proposals.audit(db, "draft_export", pid, revision, "exported, not sent")
            return {"draft_id": draft["id"]}
        except Exception:
            # Persist ambiguity; retries reconcile rather than blindly create another draft.
            with self.store.transaction() as db:
                db.execute(
                    "UPDATE proposals SET export_state=? WHERE id=?",
                    ("unknown" if attempted else None, pid),
                )
            raise
