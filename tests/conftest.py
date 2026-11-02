import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qareen_core.retrieval import initialize_chunks
from qareen_core.store import Store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("QAREEN_DATA_DIR", str(tmp_path / "private"))
    db = Store(tmp_path / "events.db")
    initialize_chunks(db)
    yield db
    db.close()
