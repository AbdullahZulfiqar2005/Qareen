import struct
import threading

import pytest
import requests

from embedding_server import BoundedHTTPServer, Service, format_web_event, pack_embedding


@pytest.fixture
def http_service(tmp_path, monkeypatch):
    monkeypatch.setenv("QAREEN_DATA_DIR", str(tmp_path / "private"))
    service = Service(
        tmp_path / "http.db", demo=True, admin_token="test-admin", ingest_token="test-ingest"
    )
    server = BoundedHTTPServer(("127.0.0.1", 0), service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", service
    server.shutdown()
    server.server_close()
    thread.join()
    service.close()


def test_pack_embedding():
    assert struct.unpack("<3f", pack_embedding([1, -1, 0.5])) == (1, -1, 0.5)


def test_legacy_format_does_not_infer_sent():
    assert format_web_event("whatsapp", "Alice", "viewed text", "You") == "viewed text"


def test_auth_roles_origins_and_host(http_service):
    url, _ = http_service
    assert requests.get(url + "/health").status_code == 200
    for headers in [
        {},
        {"Authorization": "Bearer test-ingest"},
        {"Authorization": "Bearer test-admin", "Origin": "https://evil.example"},
        {"Authorization": "Bearer test-admin", "Host": "evil.example"},
    ]:
        assert requests.get(url + "/api/events", headers=headers).status_code == 403
    headers = {"Authorization": "Bearer test-ingest"}
    assert (
        requests.post(
            url + "/ingest", headers=headers, json={"event_type": "chatgpt", "content": "hi"}
        ).status_code
        == 200
    )
    assert (
        requests.post(url + "/api/delete", headers=headers, json={"all": True}).status_code == 403
    )
    assert (
        requests.get(url + "/api/events", headers={"Authorization": "Bearer test-admin"}).json()[
            "total"
        ]
        == 1
    )


def test_malformed_ingest_and_size(http_service):
    url, _ = http_service
    h = {"Authorization": "Bearer test-admin"}
    assert requests.post(url + "/ingest", headers=h, json=[]).status_code == 400
    assert (
        requests.post(url + "/ingest", headers=h, json={"content": "x" * 260000}).status_code == 413
    )
    assert (
        requests.post(
            url + "/ingest", headers=h, json={"event_type": "unknown", "content": "x"}
        ).status_code
        == 400
    )


def test_dashboard_csp_and_static_paths(http_service):
    url, _ = http_service
    page = requests.get(url + "/")
    assert page.status_code == 200
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
    assert requests.get(url + "/dashboard/app.js").status_code == 200
    assert requests.get(url + "/dashboard/../../qareen_core/store.py").status_code != 200


def test_empty_and_cloud_off_answers_abstain(http_service):
    _, service = http_service
    assert service.ask({"query": "migration"})["abstain"]
    service.store.ingest({"event_type": "command", "content": "migration complete"})
    answer = service.ask({"query": "migration"})
    assert answer["abstain"] and len(answer["results"]) == 1
    assert "Enable cloud sharing" in answer["answer"]
