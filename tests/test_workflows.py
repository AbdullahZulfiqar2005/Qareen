import base64

import pytest

from qareen_core.gmail import Gmail, body_text
from qareen_core.workflows import ModelProvider, Proposals, run_job


class Provider:
    def generate(self, task, evidence, examples=None, question=""):
        return {
            "content": "Thanks, I will review the notes.",
            "evidence_ids": [evidence[0]["id"]],
            "abstain": False,
        }


def incoming(store, source="gmail", sid="m1"):
    return store.ingest(
        {
            "event_type": source,
            "source_event_id": sid,
            "account_id": "me@example.com",
            "conversation_id": "thread",
            "actor_id": "alice@example.com",
            "content": "Please review the notes.",
            "action": "received",
            "direction": "incoming",
            "metadata": {
                "eligible": True,
                "thread_id": "thread",
                "thread_message_ids": ["m1"],
                "subject": "Notes",
                "message_id_header": "<original@example.com>",
            },
        }
    )["id"]


def test_approval_revision_and_new_evidence(store):
    service = Proposals(store, Provider())
    p = service.create(incoming(store))
    p = service.decide(p["id"], 1, "approved")
    assert p["status"] == "approved"
    p = service.decide(p["id"], 1, "edited", "Edited reply")
    assert p["revision"] == 2 and p["status"] == "proposed"
    with pytest.raises(ValueError):
        service.decide(p["id"], 1, "approved")
    incoming(store, sid="m2")
    with pytest.raises(ValueError):
        service.decide(p["id"], 2, "approved")
    assert service.get(p["id"])["status"] == "expired"


def test_delete_cascades_feedback_and_jobs(store):
    store.update_settings({"auto_proposals": True})
    eid = incoming(store)
    service = Proposals(store, Provider())
    p = service.create(eid)
    service.decide(p["id"], 1, "approved")
    store.delete([eid])
    for table in ["proposals", "feedback", "jobs"]:
        assert store.db.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0


def test_provider_disabled_before_network(store, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-real-key")
    with pytest.raises(ValueError, match="Cloud sharing"):
        ModelProvider(store).generate("reply", [store.get_event(incoming(store))])
    assert store.db.execute("SELECT count(*) FROM usage").fetchone()[0] == 0


def test_budget_blocks_generation(store, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-real-key")
    store.update_settings({"cloud_sources": ["gmail"], "daily_call_limit": 0})
    with pytest.raises(ValueError, match="budget"):
        ModelProvider(store).generate("reply", [store.get_event(incoming(store))])


def test_worker_reconciles_duplicate_jobs(store):
    store.update_settings({"auto_proposals": True})
    incoming(store)
    service = Proposals(store, Provider())
    assert run_job(store, service)
    with store.transaction() as db:
        db.execute("UPDATE jobs SET status='pending'")
    run_job(store, service)
    assert len(service.list()) == 1


def test_gmail_approval_required_and_export_idempotent(store):
    calls = []

    def request(method, path, body=None):
        calls.append((method, path, body))
        if path.startswith("threads/"):
            return {"messages": [{"id": "m1"}]}
        if path == "profile":
            return {"emailAddress": "me@example.com"}
        if path == "drafts":
            return {"id": "d1"}
        raise AssertionError(path)

    service = Proposals(store, Provider())
    p = service.create(incoming(store))
    gmail = Gmail(store, request)
    with pytest.raises(ValueError):
        gmail.export_draft(service, p["id"], 1)
    assert not calls
    service.decide(p["id"], 1, "approved")
    assert gmail.export_draft(service, p["id"], 1) == {"draft_id": "d1"}
    assert gmail.export_draft(service, p["id"], 1) == {"draft_id": "d1"}
    posts = [x for x in calls if x[0] == "POST"]
    assert len(posts) == 1 and posts[0][1] == "drafts"
    mail = base64.urlsafe_b64decode(posts[0][2]["message"]["raw"]).decode()
    assert "To: alice@example.com" in mail and "Thanks" in mail
    assert all("send" not in x[1] for x in calls)


def test_export_timeout_reconciles_without_second_post(store):
    posts = []

    def request(method, path, body=None):
        if path.startswith("threads/"):
            return {"messages": [{"id": "m1"}]}
        if path == "profile":
            return {"emailAddress": "me@example.com"}
        if method == "POST":
            posts.append(path)
            raise TimeoutError()
        if path.startswith("drafts?"):
            return {"drafts": [{"id": "recovered"}]}
        raise AssertionError(path)

    service = Proposals(store, Provider())
    p = service.create(incoming(store))
    service.decide(p["id"], 1, "approved")
    gmail = Gmail(store, request)
    with pytest.raises(TimeoutError):
        gmail.export_draft(service, p["id"], 1)
    assert service.get(p["id"])["export_state"] == "unknown"
    assert gmail.export_draft(service, p["id"], 1)["draft_id"] == "recovered"
    assert len(posts) == 1


def test_thread_changed_prevents_export(store):
    service = Proposals(store, Provider())
    p = service.create(incoming(store))
    service.decide(p["id"], 1, "approved")

    def request(method, path, body=None):
        assert method == "GET"
        return {"messages": [{"id": "m1"}, {"id": "m2"}]}

    with pytest.raises(ValueError, match="Thread changed"):
        Gmail(store, request).export_draft(service, p["id"], 1)
    assert service.get(p["id"])["export_state"] is None


def test_html_body_excludes_scripts_and_attachments():
    data = base64.urlsafe_b64encode(b"<p>Hello</p><script>steal()</script>").decode()
    assert body_text({"mimeType": "text/html", "body": {"data": data}}).strip() == "Hello"
    assert (
        body_text({"mimeType": "text/html", "body": {"data": data}, "filename": "attachment.html"})
        == ""
    )


def test_sync_cursor_not_advanced_on_partial_failure(store):
    store.update_settings({"gmail_enabled": True})

    def request(method, path, body=None):
        if path == "profile":
            return {"emailAddress": "me@example.com", "historyId": "50"}
        if path.startswith("messages?"):
            return {"messages": [{"id": "m1"}]}
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        Gmail(store, request).sync()
    assert store.db.execute("SELECT count(*) FROM connector_cursors").fetchone()[0] == 0


def test_generation_race_rejects_stale_proposal(store):
    eid = incoming(store)

    class Racing(Provider):
        def generate(self, *args, **kwargs):
            incoming(store, sid="m2")
            return super().generate(*args, **kwargs)

    with pytest.raises(ValueError, match="changed"):
        Proposals(store, Racing()).create(eid)
    assert store.db.execute("SELECT count(*) FROM proposals").fetchone()[0] == 0


def test_cross_account_conversation_isolation(store):
    eid = incoming(store)
    for i in range(25):
        store.ingest(
            {
                "event_type": "gmail",
                "account_id": "other@example.com",
                "conversation_id": "thread",
                "content": "private other account",
                "source_event_id": str(i),
            }
        )

    class Checking(Provider):
        def generate(self, task, evidence, *args, **kwargs):
            assert len(evidence) == 1 and evidence[0]["account_id"] == "me@example.com"
            return super().generate(task, evidence, *args, **kwargs)

    Proposals(store, Checking()).create(eid)


def test_sync_skips_unsent_drafts_and_commits_after_events(store):
    store.update_settings({"gmail_enabled": True, "auto_proposals": True})

    def message(mid, labels):
        return {
            "id": mid,
            "threadId": "thread",
            "labelIds": labels,
            "internalDate": "1793534400000",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Alice <alice@example.com>"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Subject", "value": "Notes"},
                ],
                "body": {"data": base64.urlsafe_b64encode(b"Please review.").decode()},
            },
        }

    def request(method, path, body=None):
        if path == "profile":
            return {"emailAddress": "me@example.com", "historyId": "50"}
        if path.startswith("messages?"):
            return {"messages": [{"id": "m1"}]}
        if path.startswith("messages/"):
            return {"threadId": "thread"}
        if path.startswith("threads/"):
            return {"messages": [message("m1", ["INBOX"]), message("draft", ["DRAFT"])]}
        raise AssertionError(path)

    assert Gmail(store, request).sync() == 1
    assert store.events()["total"] == 1
    assert store.db.execute("SELECT cursor FROM connector_cursors").fetchone()[0] == "50"
    assert store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
