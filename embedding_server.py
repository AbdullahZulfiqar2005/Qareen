import os
import sys
import json
import struct
import threading
import lz4.block
import sqlite3
import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

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
    except Exception as e:
        print(f"Warning: Failed to load Arch Wiki FAISS index: {str(e)}")


def pack_embedding(embedding):
    """Pack a list of floats into a little-endian float32 blob - the same
    compact binary format main.go writes/reads, instead of a JSON array of
    float64 text (~5x smaller on disk, no JSON parsing needed to read back)."""
    return struct.pack(f"<{len(embedding)}f", *embedding)


def find_firefox_profile():
    base_dir = os.path.expanduser("~/.mozilla/firefox")
    if not os.path.exists(base_dir):
        return None
    for name in os.listdir(base_dir):
        profile_path = os.path.join(base_dir, name)
        if os.path.isdir(profile_path) and os.path.exists(os.path.join(profile_path, "places.sqlite")):
            if "default-release" in name:
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
            except Exception as e:
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(f"Error: {str(e)}".encode('utf-8'))
                
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
            except Exception as e:
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(f"Error: {str(e)}".encode('utf-8'))
                
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
                
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                db_content = ""
                if site == "chatgpt":
                    db_content = f"Prompted ChatGPT in conversation '{target}': {content}"
                elif site == "whatsapp":
                    if not sender or sender == "You":
                        db_content = f"Sent WhatsApp message to {target}: {content}"
                    else:
                        if sender == target:
                            db_content = f"Received WhatsApp message from {target}: {content}"
                        else:
                            db_content = f"Received WhatsApp message from {sender} in group '{target}': {content}"
                elif site == "google-meet":
                    db_content = f"Google Meet meeting transcript: {sender} said: {content}"
                else:
                    db_content = f"Web event on {site}: {content}"
                
                # Generate embedding based on the descriptive event
                formatted_text = f"[{timestamp}] [{site.upper()}] [{target}] {db_content}"
                embedding = model.embed_query(formatted_text)
                embedding_blob = pack_embedding(embedding)

                # Write using the shared connection (opening a fresh sqlite3
                # connection per request was wasted overhead under frequent
                # browser activity) guarded by a lock since it's shared across
                # ThreadingHTTPServer's worker threads.
                with _db_lock:
                    _db_conn.execute(
                        "INSERT INTO events (timestamp, event_type, app, content, embedding) VALUES (?, ?, ?, ?, ?)",
                        (timestamp, site, target, db_content, embedding_blob),
                    )
                    _db_conn.commit()
                
                print(f"[Web Log] Successfully saved {site} event: {content[:50]}...")
                
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "success"}')
            except Exception as e:
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(f"Error: {str(e)}".encode('utf-8'))
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
            except Exception as e:
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(f"Error: {str(e)}".encode('utf-8'))
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
