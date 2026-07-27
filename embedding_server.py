import datetime
import json
import os
import sqlite3
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import lz4.block
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings

# GROQ_API_KEY is only needed by chat.py / hyprland_monitor.py / main.go, not
# by this server (it never calls Groq itself). We intentionally do NOT set a
# fallback key here - a hardcoded key baked into source and shipped in a repo
# is a leaked credential the moment it's committed. If you see a default key
# anywhere in this project's history, treat it as compromised: revoke it at
# https://console.groq.com/keys and issue a fresh one, then export it as an
# environment variable (e.g. in ~/.zshrc): export GROQ_API_KEY="..."

print("Loading sentence-transformer embedding model...")
model = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
print("Model loaded successfully. Starting embedding server on 127.0.0.1:2846...")

DB_PATH = os.path.expanduser("~/.qareen.db")
# One shared connection reused across requests instead of opening a brand new
# sqlite3 connection on every single /log-web call (connection setup has real
# overhead, and unnecessary file opens/closes add up under frequent browser
# activity). check_same_thread=False + an explicit lock makes this safe to
# share across the handler threads spawned by ThreadingHTTPServer.
_db_lock = threading.Lock()
_db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_db_conn.execute("""CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    event_type TEXT,
    app TEXT,
    content TEXT,
    embedding BLOB
)""")
_db_conn.commit()

# Load local Arch Wiki FAISS index (resolve absolute path relative to the script location)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WIKI_INDEX_PATH = os.path.join(SCRIPT_DIR, "arch_wiki_index")
_wiki_store = None
if os.path.exists(WIKI_INDEX_PATH):
    print("Loading local Arch Wiki FAISS index...")
    try:
        _wiki_store = FAISS.load_local(WIKI_INDEX_PATH, model, allow_dangerous_deserialization=True)
        print("Arch Wiki FAISS index loaded successfully.")
    except Exception as e:  # noqa: BLE001
        print(f"Warning: Failed to load Arch Wiki FAISS index: {e!s}")

# --- Event FAISS Index ---
EVENT_INDEX_PATH = os.path.expanduser("~/.qareen_event_index")
_event_index_lock = threading.Lock()
_event_store = None
_last_indexed_id = 0
_event_index_dirty = False
_event_insert_count = 0


def _unpack_blob(blob):
    """Unpack a binary embedding BLOB into a list of floats."""
    if not blob or len(blob) % 4 != 0:
        return None
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def _load_or_build_event_index():
    """Load existing event FAISS index from disk, or build from SQLite."""
    global _event_store, _last_indexed_id

    index_file = os.path.join(EVENT_INDEX_PATH, "index.faiss")
    meta_file = os.path.join(EVENT_INDEX_PATH, "meta.json")

    if os.path.exists(index_file):
        print("Loading event FAISS index from disk...")
        try:
            _event_store = FAISS.load_local(
                EVENT_INDEX_PATH, model, allow_dangerous_deserialization=True
            )
            if os.path.exists(meta_file):
                with open(meta_file) as f:
                    meta = json.load(f)
                    _last_indexed_id = meta.get("last_indexed_id", 0)
            print(f"Event index loaded (last_indexed_id={_last_indexed_id}).")
            return
        except Exception as e:  # noqa: BLE001
            print(f"Warning: Failed to load event index, rebuilding: {e}")

    print("Building event FAISS index from database...")
    with _db_lock:
        cursor = _db_conn.execute(
            "SELECT id, timestamp, event_type, app, content, embedding "
            "FROM events WHERE embedding IS NOT NULL ORDER BY id"
        )
        rows = cursor.fetchall()

    if not rows:
        print("No events with embeddings found. Index will be created on first insert.")
        return

    texts = []
    embeddings = []
    metadatas = []
    max_id = 0

    for row_id, ts, etype, app, content, blob in rows:
        vec = _unpack_blob(blob)
        if vec is None or len(vec) != 384:
            continue
        texts.append(content)
        embeddings.append(vec)
        metadatas.append({
            "event_id": row_id,
            "timestamp": ts,
            "event_type": etype,
            "app": app,
        })
        max_id = row_id

    if texts:
        _event_store = FAISS.from_embeddings(
            list(zip(texts, embeddings)),
            model,
            metadatas=metadatas,
        )
        _last_indexed_id = max_id
        _save_event_index()
        print(f"Event index built with {len(texts)} events (last_id={max_id}).")
    else:
        print("No valid embeddings found. Index will be created on first insert.")


def _save_event_index():
    """Persist the event FAISS index and metadata to disk."""
    global _event_index_dirty
    if _event_store is None:
        return
    os.makedirs(EVENT_INDEX_PATH, exist_ok=True)
    _event_store.save_local(EVENT_INDEX_PATH)
    with open(os.path.join(EVENT_INDEX_PATH, "meta.json"), "w") as f:
        json.dump({"last_indexed_id": _last_indexed_id}, f)
    _event_index_dirty = False


def _sync_new_events():
    """Index any events inserted by the Go daemon since last sync."""
    global _event_store, _last_indexed_id, _event_index_dirty

    with _db_lock:
        cursor = _db_conn.execute(
            "SELECT id, timestamp, event_type, app, content, embedding "
            "FROM events WHERE id > ? AND embedding IS NOT NULL ORDER BY id",
            (_last_indexed_id,),
        )
        rows = cursor.fetchall()

    if not rows:
        return

    texts = []
    embeddings = []
    metadatas = []
    max_id = _last_indexed_id

    for row_id, ts, etype, app, content, blob in rows:
        vec = _unpack_blob(blob)
        if vec is None or len(vec) != 384:
            continue
        texts.append(content)
        embeddings.append(vec)
        metadatas.append({
            "event_id": row_id,
            "timestamp": ts,
            "event_type": etype,
            "app": app,
        })
        max_id = row_id

    if not texts:
        return

    if _event_store is None:
        _event_store = FAISS.from_embeddings(
            list(zip(texts, embeddings)),
            model,
            metadatas=metadatas,
        )
    else:
        _event_store.add_embeddings(
            list(zip(texts, embeddings)),
            metadatas=metadatas,
        )

    _last_indexed_id = max_id
    _event_index_dirty = True


def _add_event_to_index(event_id, timestamp, event_type, app, content, embedding_vec):
    """Add a single newly inserted event to the in-memory FAISS index."""
    global _event_store, _last_indexed_id, _event_index_dirty, _event_insert_count

    meta = {
        "event_id": event_id,
        "timestamp": timestamp,
        "event_type": event_type,
        "app": app,
    }

    if _event_store is None:
        _event_store = FAISS.from_embeddings(
            [(content, embedding_vec)],
            model,
            metadatas=[meta],
        )
    else:
        _event_store.add_embeddings(
            [(content, embedding_vec)],
            metadatas=[meta],
        )

    _last_indexed_id = max(_last_indexed_id, event_id)
    _event_index_dirty = True
    _event_insert_count += 1

    if _event_insert_count % 50 == 0:
        with _event_index_lock:
            _save_event_index()


def _background_index_saver():
    """Periodically save the event index to disk if dirty."""
    while True:
        time.sleep(60)
        with _event_index_lock:
            if _event_index_dirty:
                try:
                    _save_event_index()
                except Exception as e:  # noqa: BLE001
                    print(f"Warning: Failed to save event index: {e}")


_load_or_build_event_index()
_saver_thread = threading.Thread(target=_background_index_saver, daemon=True)
_saver_thread.start()


def pack_embedding(embedding):
    """Pack a list of floats into a little-endian float32 blob - the same
    compact binary format main.go writes/reads, instead of a JSON array of
    float64 text (~5x smaller on disk, no JSON parsing needed to read back)."""
    return struct.pack(f"<{len(embedding)}f", *embedding)


def format_web_event(site, target, content, sender):
    """Format a browser extension event into a human-readable string for storage."""
    ai_site_names = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini", "grok": "Grok"}
    if site in ai_site_names:
        pretty = ai_site_names[site]
        response_prefix = f"{pretty} response: "
        if content.startswith(response_prefix):
            reply_text = content[len(response_prefix):]
            return f"{pretty} replied in conversation '{target}': {reply_text}"
        else:
            return f"Prompted {pretty} in conversation '{target}': {content}"
    elif site == "whatsapp":
        if not sender or sender == "You":
            return f"Sent WhatsApp message to {target}: {content}"
        else:
            if sender == target:
                return f"Received WhatsApp message from {target}: {content}"
            else:
                return f"Received WhatsApp message from {sender} in group '{target}': {content}"
    elif site == "google-meet":
        return f"Google Meet meeting transcript: {sender} said: {content}"
    elif site == "linkedin":
        return f"Sent LinkedIn message to {target}: {content}"
    elif site == "instagram":
        return f"Sent Instagram DM to {target}: {content}"
    elif site == "reddit" or site == "github" or site == "gmail":
        return content
    else:
        return f"Web event on {site}: {content}"


def find_firefox_profile():
    base_dir = os.path.expanduser("~/.mozilla/firefox")
    if not os.path.exists(base_dir):
        return None
    for name in os.listdir(base_dir):
        profile_path = os.path.join(base_dir, name)
        if os.path.isdir(profile_path) and os.path.exists(os.path.join(profile_path, "places.sqlite")) and "default-release" in name:
            return profile_path
    for name in os.listdir(base_dir):
        profile_path = os.path.join(base_dir, name)
        if os.path.isdir(profile_path) and os.path.exists(os.path.join(profile_path, "places.sqlite")):
            return profile_path
    return None

class EmbeddingHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        # Handle CORS preflight requests from browser extension
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        if self.path == '/embed':
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))
                
                texts = data.get('texts', [])
                if not isinstance(texts, list):
                    texts = [texts]
                    
                if not texts:
                    self.send_response(400)
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(b"Error: 'texts' list cannot be empty.")
                    return
                    
                embeddings = model.embed_documents(texts)
                
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'embeddings': embeddings}).encode('utf-8'))
            except Exception as e:  # noqa: BLE001
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(f"Error: {e!s}".encode())
                
        elif self.path == '/wiki-search':
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))
                
                query = data.get('query', '')
                k = data.get('k', 3)
                
                results = []
                if _wiki_store and query:
                    docs = _wiki_store.similarity_search(query, k=k)
                    for doc in docs:
                        results.append({
                            'content': doc.page_content,
                            'source': doc.metadata.get('source', 'Unknown')
                        })
                
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'results': results}).encode('utf-8'))
            except Exception as e:  # noqa: BLE001
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(f"Error: {e!s}".encode())
                
        elif self.path == '/search':
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))

                query = data.get('query', '')
                k = data.get('k', 12)

                if not query:
                    self.send_response(400)
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(b"Error: 'query' cannot be empty.")
                    return

                with _event_index_lock:
                    _sync_new_events()

                results = []
                if _event_store is not None:
                    with _event_index_lock:
                        docs_and_scores = _event_store.similarity_search_with_score(query, k=k)
                    for doc, score in docs_and_scores:
                        similarity = 1.0 - float(score)
                        results.append({
                            'event_id': doc.metadata.get('event_id', 0),
                            'timestamp': doc.metadata.get('timestamp', ''),
                            'event_type': doc.metadata.get('event_type', ''),
                            'app': doc.metadata.get('app', ''),
                            'content': doc.page_content,
                            'score': similarity,
                        })

                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'results': results}).encode('utf-8'))
            except Exception as e:  # noqa: BLE001
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(f"Error: {e!s}".encode())

        elif self.path == '/log-web':
            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))
                
                site = data.get('site', '')
                target = data.get('target', '')
                content = data.get('content', '')
                sender = data.get('sender', '')
                
                if not site or not content:
                    self.send_response(400)
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(b"Error: 'site' and 'content' cannot be empty.")
                    return
                
                timestamp = datetime.datetime.now(datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
                db_content = format_web_event(site, target, content, sender)
                
                # Generate embedding based on the descriptive event
                formatted_text = f"[{timestamp}] [{site.upper()}] [{target}] {db_content}"
                embedding = model.embed_query(formatted_text)
                embedding_blob = pack_embedding(embedding)

                # Write using the shared connection (opening a fresh sqlite3
                # connection per request was wasted overhead under frequent
                # browser activity) guarded by a lock since it's shared across
                # ThreadingHTTPServer's worker threads.
                with _db_lock:
                    cursor = _db_conn.execute(
                        "INSERT INTO events (timestamp, event_type, app, content, embedding) VALUES (?, ?, ?, ?, ?)",
                        (timestamp, site, target, db_content, embedding_blob),
                    )
                    new_id = cursor.lastrowid
                    _db_conn.commit()

                with _event_index_lock:
                    _add_event_to_index(
                        new_id, timestamp, site, target, db_content, embedding
                    )
                
                print(f"[Web Log] Successfully saved {site} event: {content[:50]}...")
                
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "success"}')
            except Exception as e:  # noqa: BLE001
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(f"Error: {e!s}".encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == '/firefox-tabs':
            try:
                profile_path = find_firefox_profile()
                if not profile_path:
                    self.send_response(404)
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(b"Error: Firefox profile not found.")
                    return
                    
                session_file = os.path.join(profile_path, "sessionstore-backups/recovery.jsonlz4")
                if not os.path.exists(session_file):
                    self.send_response(404)
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(b"Error: Firefox session recovery file not found.")
                    return
                    
                with open(session_file, "rb") as f:
                    magic = f.read(8)
                    if magic == b"mozLz40\0":
                        compressed_data = f.read()
                        decompressed_data = lz4.block.decompress(compressed_data)
                        session_data = json.loads(decompressed_data.decode("utf-8"))
                        
                        tabs_list = []
                        for window in session_data.get("windows", []):
                            for tab in window.get("tabs", []):
                                entries = tab.get("entries", [])
                                if entries:
                                    i = int(tab.get("index", 1)) - 1
                                    i = max(0, min(len(entries) - 1, i))
                                    entry = entries[i]
                                    tabs_list.append({
                                        'title': entry.get('title', ''),
                                        'url': entry.get('url', '')
                                    })
                                    
                        self.send_response(200)
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({'tabs': tabs_list}).encode('utf-8'))
                    else:
                        self.send_response(400)
                        self.send_header('Access-Control-Allow-Origin', '*')
                        self.end_headers()
                        self.wfile.write(b"Error: Invalid Firefox session file magic bytes.")
            except Exception as e:  # noqa: BLE001
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(f"Error: {e!s}".encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress request log printouts in terminal to keep it clean
        pass

def run():
    # ThreadingHTTPServer handles the Go daemon's /embed calls and the browser
    # extension's /log-web calls concurrently instead of queueing them one at
    # a time behind a single-threaded HTTPServer, which caused noticeable
    # delays when both were active at once (e.g. typing fast in WhatsApp while
    # the daemon is mid-query).
    server = ThreadingHTTPServer(('127.0.0.1', 2846), EmbeddingHandler)
    server.serve_forever()

if __name__ == '__main__':
    run()
