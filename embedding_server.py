"""Authenticated local service. Importing this module has no filesystem/thread effects."""

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import signal
import struct
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from qareen_core.config import data_dir, token
from qareen_core.gmail import Gmail
from qareen_core.retrieval import Embeddings, enrich_once, initialize_chunks, search, time_bounds
from qareen_core.store import Store
from qareen_core.workflows import ModelProvider, Proposals, run_job


def pack_embedding(embedding):
    return struct.pack(f"<{len(embedding)}f", *embedding)


def format_web_event(site, target, content, sender):
    """Legacy display helper. v2 stores action and actors separately; never infers sent."""
    return content


def _is_temporal_query(query):
    return time_bounds(query)[0] is not None


def _get_temporal_start_time(query):
    return time_bounds(query)[0]


class Service:
    def __init__(self, path=None, demo=False, admin_token=None, ingest_token=None, model=None):
        self.store = Store(path)
        self.model = model or Embeddings(demo)
        initialize_chunks(self.store)
        self.admin_token = admin_token or token("admin")
        self.ingest_token = ingest_token or token("ingest")
        self.store.recover_exports()
        self.proposals = Proposals(self.store)
        self.gmail = Gmail(self.store)
        self.stop_event = threading.Event()
        self.workers = []
        self.health = {"embeddings": "pending", "gmail": "disabled"}
        self.root = Path(__file__).resolve().parent
        self.started = time.monotonic()

    def start(self):
        for target in (self.enrichment_loop, self.workflow_loop):
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            self.workers.append(thread)

    def enrichment_loop(self):
        while not self.stop_event.is_set():
            try:
                active = enrich_once(self.store, self.model)
                self.health["embeddings"] = "ok" if active else "idle or retry pending"
            except Exception as error:
                self.health["embeddings"] = type(error).__name__
            self.stop_event.wait(2)

    def workflow_loop(self):
        last_sync = 0
        last_prune = 0
        while not self.stop_event.is_set():
            try:
                settings = self.store.settings()
                if not settings["paused"]:
                    if settings["auto_proposals"]:
                        self.store.schedule_incidents()
                        run_job(self.store, self.proposals)
                    if (
                        settings["gmail_enabled"]
                        and time.monotonic() - last_sync >= settings["gmail_poll_seconds"]
                    ):
                        last_sync = time.monotonic()
                        try:
                            self.gmail.sync()
                            self.health["gmail"] = "ok"
                        except Exception as error:
                            self.health["gmail"] = type(error).__name__
                    if time.monotonic() - last_prune > 3600:
                        cutoff = (
                            dt.datetime.now(dt.timezone.utc)
                            - dt.timedelta(days=settings["retention_days"])
                        ).strftime("%Y-%m-%d %H:%M:%S")
                        self.store.delete(older_than=cutoff)
                        last_prune = time.monotonic()
            except Exception as error:
                self.health["worker"] = type(error).__name__
            self.stop_event.wait(2)

    def close(self):
        self.stop_event.set()
        for worker in self.workers:
            worker.join(timeout=50)
        self.store.close()

    def ask(self, body):
        q = body.get("query", "")
        result = search(
            self.store, self.model, q, k=40, source=body.get("source", ""), app=body.get("app", "")
        )
        evidence = result["results"]
        if not evidence:
            result["answer"] = (
                "No supporting personal events found. I cannot say what you did previously."
            )
            result["abstain"] = True
            if body.get("documentation") is True:
                result["documentation"] = self.wiki(q)
                result["answer"] += (
                    " Documentation below describes possible fixes, not your past actions."
                )
            return result
        allowed = set(self.store.settings()["cloud_sources"])
        shareable = [e for e in evidence if e["event_type"] in allowed]
        result["coverage"]["cloud_excluded"] = len(evidence) - len(shareable)
        if not shareable:
            result["answer"] = (
                "Local evidence is available below. Enable cloud sharing for selected sources to generate an answer."
            )
            result["abstain"] = True
            return result
        output = ModelProvider(self.store).generate("answer", shareable, question=q)
        result["answer"] = output["content"]
        result["abstain"] = output["abstain"]
        result["evidence_ids"] = output["evidence_ids"]
        return result

    def wiki(self, query, k=3):
        # Operator-generated trusted local index only, never supplied by HTTP callers.
        path = Path(os.getenv("QAREEN_WIKI_INDEX", str(data_dir() / "arch_wiki_index")))
        if not path.exists():
            return {"results": [], "available": False}
        if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o022:
            raise ValueError("Wiki index directory must be privately owned")
        for name in ("index.pkl", "index.faiss"):
            file = path / name
            if (
                file.is_symlink()
                or file.stat().st_uid != os.getuid()
                or file.stat().st_mode & 0o022
            ):
                raise ValueError("Wiki index files must be privately owned")
        from langchain_community.vectorstores import FAISS

        from qareen_embeddings import RemoteEmbeddings

        store = FAISS.load_local(
            str(path), RemoteEmbeddings(), allow_dangerous_deserialization=True
        )
        return {
            "results": [
                {"content": d.page_content, "source": d.metadata.get("source", "")}
                for d in store.similarity_search(query, k=min(int(k), 5))
            ],
            "available": True,
        }


class BoundedHTTPServer(HTTPServer):
    def __init__(self, address, service):
        self.service = service
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        self.pool.submit(self.finish_in_pool, request, address)

    def finish_in_pool(self, request, address):
        try:
            self.finish_request(request, address)
        finally:
            self.shutdown_request(request)
            self.slots.release()

    def server_close(self):
        super().server_close()
        self.pool.shutdown(wait=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "Qareen/2"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # Request bodies, query strings and credentials never enter operational logs.

    @property
    def app(self):
        return self.server.service

    def origin_allowed(self):
        origin = self.headers.get("Origin", "")
        port = self.server.server_port
        return (
            not origin
            or origin
            in {
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
                "http://127.0.0.1:2847",
                "http://localhost:2847",
            }
            or origin.startswith(("moz-extension://", "chrome-extension://"))
        )

    def authorized(self, ingest=False):
        import hmac

        host = self.headers.get("Host", "")
        if (
            host
            not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            or not self.origin_allowed()
        ):
            return False
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
        return hmac.compare_digest(supplied.encode(), self.app.admin_token.encode()) or (
            ingest and hmac.compare_digest(supplied.encode(), self.app.ingest_token.encode())
        )

    def reply(self, status, data):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        origin = self.headers.get("Origin")
        if origin and self.origin_allowed():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        if not self.origin_allowed():
            return self.reply(403, {"error": "Origin rejected"})
        self.send_response(204)
        if self.headers.get("Origin"):
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/health":
            return self.reply(200, {"service": "qareen", "version": 2, "ready": True})
        if path == "/" or path.startswith("/dashboard/"):
            return self.static(path)
        if not self.authorized():
            return self.reply(403, {"error": "Authentication or origin rejected"})
        try:
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if path == "/api/events":
                result = self.app.store.events(
                    params.get("limit", 50),
                    params.get("offset", 0),
                    params.get("type", ""),
                    params.get("app", ""),
                )
            elif path == "/api/event":
                result = self.app.store.get_event(int(params["id"]))
            elif path == "/api/stats":
                result = dict(self.app.store.stats(), health=self.app.health)
            elif path == "/api/settings":
                result = self.app.store.settings()
            elif path == "/api/proposals":
                result = {"proposals": self.app.proposals.list()}
            elif path == "/api/search":
                result = search(
                    self.app.store,
                    self.app.model,
                    params.get("q", ""),
                    params.get("k", 12),
                    params.get("source", ""),
                    params.get("app", ""),
                )
            elif path == "/api/export":
                result = {"events": []}
                offset = 0
                while True:
                    page = self.app.store.events(500, offset)["events"]
                    result["events"].extend(page)
                    offset += 500
                    if len(page) < 500:
                        break
                    if offset >= 100000:
                        raise ValueError(
                            "Export is limited to 100000 events; use a SQLite backup for larger histories"
                        )
            elif path == "/firefox-tabs":
                result = firefox_tabs()
            else:
                return self.reply(404, {"error": "Not found"})
            self.reply(200, result)
        except (ValueError, KeyError, TypeError) as error:
            self.reply(400, {"error": str(error)})
        except Exception as error:
            self.reply(503, {"error": type(error).__name__, "retryable": True})

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if not self.authorized(ingest=path in {"/log-web", "/ingest"}):
            return self.reply(403, {"error": "Authentication or origin rejected"})
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Chunked requests unsupported")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 256000:
                return self.reply(413, {"error": "Body must contain 1–256000 bytes"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("Use application/json")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            if path in {"/log-web", "/ingest"}:
                if self.headers.get(
                    "Authorization"
                ) == "Bearer " + self.app.ingest_token and body.get(
                    "event_type", body.get("site")
                ) not in {"chatgpt", "whatsapp", "google-meet"}:
                    raise ValueError("Ingestion token only permits browser companion sources")
                result = self.app.store.ingest(body)
            elif path == "/embed":
                texts = body.get("texts", [])
                if not isinstance(texts, list) or not texts:
                    raise ValueError("Nonempty texts array required")
                result = {"embeddings": self.app.model.encode(texts)}
            elif path == "/search":
                result = search(
                    self.app.store,
                    self.app.model,
                    body.get("query", ""),
                    body.get("k", 12),
                    body.get("source", ""),
                    body.get("app", ""),
                    body.get("start"),
                    body.get("end"),
                )
            elif path == "/wiki-search":
                result = self.app.wiki(body.get("query", ""), body.get("k", 3))
            elif path == "/api/ask":
                result = self.app.ask(body)
            elif path == "/api/settings":
                result = self.app.store.update_settings(body)
            elif path == "/api/delete":
                if body.get("all") is not True and "ids" not in body and not body.get("before"):
                    raise ValueError("Specify ids, before, or all=true")
                if body.get("all") is True:
                    self.app.store.update_settings({"paused": True})
                    import shutil

                    outbox = data_dir() / "outbox"
                    if outbox.is_dir() and not outbox.is_symlink():
                        shutil.rmtree(outbox)
                    for session in data_dir().glob("chat-*.json"):
                        if not session.is_symlink():
                            session.unlink()
                    # Remove legacy plaintext derived copies after stopping old services.
                    import shutil

                    legacy = Path.home() / ".qareen_event_index"
                    if legacy.is_dir() and not legacy.is_symlink():
                        shutil.rmtree(legacy)
                    log = Path.home() / ".qareen.log"
                    if log.is_file() and not log.is_symlink():
                        log.unlink()
                result = {"deleted": self.app.store.delete(body.get("ids"), body.get("before"))}
            elif path == "/api/proposals/create":
                result = self.app.proposals.create(body["event_id"], body.get("type", "reply"))
            elif path == "/api/proposals/decide":
                result = self.app.proposals.decide(
                    body["id"], body["revision"], body["decision"], body.get("content")
                )
            elif path == "/api/proposals/export":
                result = self.app.gmail.export_draft(
                    self.app.proposals, body["id"], body["revision"]
                )
            elif path == "/api/gmail/sync":
                result = {"added": self.app.gmail.sync()}
            elif path == "/api/migrate":
                result = {"migrated": self.app.store.migrate_legacy(body["timezone"])}
            else:
                return self.reply(404, {"error": "Not found"})
            self.reply(200, result)
        except (ValueError, KeyError, TypeError) as error:
            self.reply(400, {"error": str(error)})
        except Exception as error:
            self.reply(503, {"error": type(error).__name__, "retryable": True})

    def static(self, path):
        names = {
            "/": "index.html",
            "/dashboard/index.html": "index.html",
            "/dashboard/app.js": "app.js",
            "/dashboard/style.css": "style.css",
        }
        if path not in names:
            return self.reply(404, {"error": "Not found"})
        file = self.app.root / "dashboard" / names[path]
        raw = file.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css"}[
                file.suffix
            ],
        )
        self.send_header("Content-Length", str(len(raw)))
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)


def find_firefox_profile():
    configured = os.getenv("QAREEN_FIREFOX_PROFILE")
    if configured:
        return str(Path(configured).expanduser())
    import configparser

    roots = [
        Path.home() / ".mozilla/firefox",
        Path.home() / "snap/firefox/common/.mozilla/firefox",
        Path.home() / ".var/app/org.mozilla.firefox/.mozilla/firefox",
    ]
    candidates = []
    for root in roots:
        ini = configparser.ConfigParser()
        ini.read(root / "profiles.ini")
        for section in ini.sections():
            if ini.has_option(section, "Default") and section.startswith("Install"):
                p = root / ini[section]["Default"]
                if (p / "places.sqlite").exists():
                    return str(p)
            if ini.has_option(section, "Path"):
                p = Path(ini[section]["Path"])
                if ini[section].get("IsRelative", "1") == "1":
                    p = root / p
                if (p / "places.sqlite").exists():
                    if ini[section].get("Default") == "1":
                        return str(p)
                    candidates.append(p)
    return str(candidates[0]) if len(candidates) == 1 else None


def firefox_tabs():
    profile = find_firefox_profile()
    if not profile:
        return {"tabs": [], "health": "No unambiguous profile; configure QAREEN_FIREFOX_PROFILE"}
    file = Path(profile) / "sessionstore-backups/recovery.jsonlz4"
    if not file.exists():
        return {"tabs": []}
    raw = file.read_bytes()
    if raw[:8] != b"mozLz40\0" or len(raw) > 30000000:
        raise ValueError("Invalid Firefox session")
    import lz4.block

    session = json.loads(lz4.block.decompress(raw[8:]))
    tabs = []
    for window in session.get("windows", []):
        if window.get("isPrivate"):
            continue
        for tab in window.get("tabs", []):
            entries = tab.get("entries", [])
            if entries:
                item = entries[max(0, min(len(entries) - 1, int(tab.get("index", 1)) - 1))]
                tabs.append({"title": item.get("title", ""), "url": item.get("url", "")})
    return {"tabs": tabs}


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--port", type=int, default=2846)
    args = parser.parse_args()
    from qareen_dotenv import load_dotenv

    load_dotenv()
    os.umask(0o077)
    app = Service(demo=args.demo)
    server = BoundedHTTPServer(("127.0.0.1", args.port), app)
    # Per-user OS lock; no import-time PID writes, no signaling arbitrary PIDs.
    import fcntl

    lock = (data_dir() / "service.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    app.start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        app.close()
        lock.close()


if __name__ == "__main__":
    run()
