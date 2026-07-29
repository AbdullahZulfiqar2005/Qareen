import os

# --- Memory & Threading Optimizations ---
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import atexit
import datetime
import gc
import json
import sqlite3
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Write PID file for status tracking compatibility
try:
    with open("/tmp/qareen_py.pid", "w") as f:
        f.write(str(os.getpid()))
except Exception as e:  # noqa: BLE001
    print(f"Warning: Failed to write PID file: {e}")

@atexit.register
def cleanup_pid():
    try:
        os.remove("/tmp/qareen_py.pid")
    except OSError:
        pass

# GROQ_API_KEY is only needed by chat.py / hyprland_monitor.py / main.go, not
# by this server (it never calls Groq itself). We intentionally do NOT set a
# fallback key here - a hardcoded key baked into source and shipped in a repo
# is a leaked credential the moment it's committed. If you see a default key
# anywhere in this project's history, treat it as compromised: revoke it at
# https://console.groq.com/keys and issue a fresh one, then export it as an
# environment variable (e.g. in ~/.zshrc): export GROQ_API_KEY="..."
import ctypes

import lz4.block

# Model On-Demand Loading & Unloading Controls
model = None
torch = None
FAISS = None
HuggingFaceEmbeddings = None
_model_lock = threading.Lock()
_unload_timer = None

def load_model():
    global model, _unload_timer
    with _model_lock:
        if _unload_timer is not None:
            _unload_timer.cancel()
            _unload_timer = None
        if model is None:
            print("[Memory Optimizer] Loading sentence-transformer embedding model on-demand...")
            import torch as _torch
            from langchain_community.vectorstores import FAISS as _FAISS
            from langchain_huggingface import (
                HuggingFaceEmbeddings as _HuggingFaceEmbeddings,
            )
            
            # Force CPU execution parameters
            _torch.set_num_threads(1)
            if hasattr(_torch, "set_grad_enabled"):
                _torch.set_grad_enabled(False)
            
            # Make them globally available
            globals()['torch'] = _torch
            globals()['FAISS'] = _FAISS
            globals()['HuggingFaceEmbeddings'] = _HuggingFaceEmbeddings
            
            model = _HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

def unload_model():
    global model, _wiki_store, _event_store
    with _model_lock:
        if model is not None:
            print("[Memory Optimizer] Unloading embedding model and indices to reclaim RAM...")
            _wiki_store = None
            _event_store = None
            model = None
            
            # Clear globals
            for name in ['torch', 'FAISS', 'HuggingFaceEmbeddings']:
                if name in globals():
                    del globals()[name]
            
            # Remove cached modules from sys.modules to release PyTorch DLLs
            import sys
            for mod in list(sys.modules.keys()):
                if any(x in mod for x in ['torch', 'langchain', 'transformers', 'sentence_transformers', 'faiss']):
                    del sys.modules[mod]
            
            gc.collect()
            try:
                libc = ctypes.CDLL("libc.so.6")
                libc.malloc_trim(0)
            except Exception as e:  # noqa: BLE001
                print(f"Warning: malloc_trim failed: {e}")
            print("[Memory Optimizer] Model and indices successfully unloaded.")

def schedule_model_unload():
    global _unload_timer
    with _model_lock:
        if _unload_timer is not None:
            _unload_timer.cancel()
        _unload_timer = threading.Timer(15.0, unload_model)
        _unload_timer.start()

print("Embedding server starting on 127.0.0.1:2846...")

DB_PATH = os.path.expanduser("~/.qareen.db")
# One shared connection reused across requests
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

# Local Arch Wiki FAISS index path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WIKI_INDEX_PATH = os.path.join(SCRIPT_DIR, "arch_wiki_index")
_wiki_store = None

def get_wiki_store():
    global _wiki_store
    if _wiki_store is None and os.path.exists(WIKI_INDEX_PATH):
        print("Loading local Arch Wiki FAISS index on-demand...")
        try:
            load_model()
            _wiki_store = FAISS.load_local(WIKI_INDEX_PATH, model, allow_dangerous_deserialization=True)
            print("Arch Wiki FAISS index loaded successfully.")
        except Exception as e:  # noqa: BLE001
            print(f"Warning: Failed to load Arch Wiki FAISS index: {e!s}")
    return _wiki_store

# --- Event FAISS Index ---
EVENT_INDEX_PATH = os.path.expanduser("~/.qareen_event_index")
_event_index_lock = threading.Lock()
_event_store = None
_last_indexed_id = 0
_event_index_dirty = False
_event_insert_count = 0

def get_event_store():
    if _event_store is None:
        load_model()
        _load_or_build_event_index()
    return _event_store


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

    global _event_store
    store = get_event_store()
    if store is None:
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

    global _event_store
    store = get_event_store()
    if store is None:
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


_saver_thread = threading.Thread(target=_background_index_saver, daemon=True)
_saver_thread.start()


def pack_embedding(embedding):
    """Pack a list of floats into a little-endian float32 blob - the same
    compact binary format main.go writes/reads, instead of a JSON array of
    float64 text (~5x smaller on disk, no JSON parsing needed to read back)."""
    return struct.pack(f"<{len(embedding)}f", *embedding)


def format_web_event(site, target, content, sender):
    """Format a browser extension event into a human-readable string for storage."""
    ai_site_names = {"chatgpt": "ChatGPT", "claude": "Claude", "gemini": "Gemini", "grok": "Grok", "groq": "Groq"}
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

def _is_temporal_query(query):
    q = query.lower().strip()
    keywords = [
        "today", "yesterday", "last hour", "last 1 hour", "last 2 hours", 
        "last 3 hours", "last 4 hours", "last 5 hours", "last 12 hours",
        "last 24 hours", "last 30 mins", "last 30 minutes", "summarize my day",
        "what did i do today", "activity today", "what i did today"
    ]
    return any(kw in q for kw in keywords)

def _get_temporal_start_time(query):
    q = query.lower().strip()
    # Use localized local timezone aware datetime
    now = datetime.datetime.now(datetime.timezone.utc).astimezone()
    
    if "yesterday" in q:
        yesterday = now - datetime.timedelta(days=1)
        return yesterday.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
    elif "last hour" in q or "last 1 hour" in q:
        return (now - datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    elif "last 2 hours" in q:
        return (now - datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    elif "last 3 hours" in q:
        return (now - datetime.timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    elif "last 4 hours" in q:
        return (now - datetime.timedelta(hours=4)).strftime("%Y-%m-%d %H:%M:%S")
    elif "last 5 hours" in q:
        return (now - datetime.timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S")
    elif "last 12 hours" in q:
        return (now - datetime.timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
    elif "last 24 hours" in q:
        return (now - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    elif "last 30 min" in q:
        return (now - datetime.timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        # Default to start of today
        return now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")

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
                    
                load_model()
                embeddings = model.embed_documents(texts)
                schedule_model_unload()
                
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
                wiki_store = get_wiki_store()
                if wiki_store and query:
                    docs = wiki_store.similarity_search(query, k=k)
                    for doc in docs:
                        results.append({
                            'content': doc.page_content,
                            'source': doc.metadata.get('source', 'Unknown')
                        })
                schedule_model_unload()
                
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
                if _is_temporal_query(query):
                    start_time = _get_temporal_start_time(query)
                    print(f"[Search Router] Temporal query detected. Fetching events since: {start_time}")
                    with _db_lock:
                        cursor = _db_conn.execute(
                            "SELECT id, timestamp, event_type, app, content FROM events WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 40",
                            (start_time,)
                        )
                        rows = cursor.fetchall()
                    for r_id, r_time, r_type, r_app, r_content in rows:
                        results.append({
                            'event_id': r_id,
                            'timestamp': r_time,
                            'event_type': r_type,
                            'app': r_app,
                            'content': r_content,
                            'score': 1.0,
                        })
                else:
                    event_store = get_event_store()
                    if event_store is not None:
                        with _event_index_lock:
                            docs_and_scores = event_store.similarity_search_with_score(query, k=k)
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
                schedule_model_unload()

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
                load_model()
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
                schedule_model_unload()
                
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
