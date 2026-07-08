"""
Shared embeddings client for Qareen's Python tools.

embedding_server.py already keeps the all-MiniLM-L6-v2 model resident in
memory as a long-running local HTTP service on 127.0.0.1:2846. Previously,
chat.py and hyprland_monitor.py each imported HuggingFaceEmbeddings directly
and loaded their *own* independent copy of the same model on top of that -
meaning up to three full sentence-transformer + PyTorch runtimes could be
resident in memory at once on an 8GB machine, each taking a noticeable
amount of RAM and several seconds to load.

RemoteEmbeddings implements the same interface FAISS expects
(embed_documents / embed_query) but delegates the actual embedding work to
the already-running server over HTTP, so these tools only need the server to
be up (which `qareen start` already guarantees) rather than loading the
model a second or third time.
"""

import requests

EMBED_SERVER_URL = "http://127.0.0.1:2846/embed"


class RemoteEmbeddings:
    """Minimal drop-in replacement for HuggingFaceEmbeddings that calls the
    local Qareen embedding server instead of loading the model in-process."""

    def __init__(self, server_url: str = EMBED_SERVER_URL, timeout: float = 30.0):
        self.server_url = server_url
        self.timeout = timeout

    def _embed(self, texts):
        try:
            resp = requests.post(self.server_url, json={"texts": texts}, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(
                "Could not reach the Qareen embedding server at "
                f"{self.server_url}. Make sure it's running first "
                "('qareen start' or 'python embedding_server.py')."
            ) from e
        return resp.json()["embeddings"]

    def embed_documents(self, texts):
        return self._embed(list(texts))

    def embed_query(self, text):
        return self._embed([text])[0]
