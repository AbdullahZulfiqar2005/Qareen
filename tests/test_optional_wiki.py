"""Optional dependency contract; lightweight CI skips this module."""

import pytest

pytest.importorskip("langchain_community")
from langchain_community.vectorstores import FAISS
from langchain_core.embeddings import Embeddings as LangChainEmbeddings

import qareen_embeddings
from qareen_core.retrieval import Embeddings


def test_production_remote_embeddings_faiss_contract(monkeypatch):
    model = Embeddings(demo=True)

    def fake_transport(path, body):
        assert path == "/embed"
        return {"embeddings": model.encode(body["texts"])}

    monkeypatch.setattr(qareen_embeddings, "call", fake_transport)
    remote = qareen_embeddings.RemoteEmbeddings()
    assert isinstance(remote, LangChainEmbeddings)
    index = FAISS.from_texts(["SQLite transaction locking", "Firefox browser tabs"], remote)
    assert (
        index.similarity_search("SQLite transaction", k=1)[0].page_content
        == "SQLite transaction locking"
    )
