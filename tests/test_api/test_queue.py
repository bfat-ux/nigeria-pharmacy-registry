"""Tests for verification queue endpoints — all DB-only."""

from __future__ import annotations


class TestQueueList:
    """GET /api/queue — requires DB + registry_read."""

    def test_requires_auth(self, client):
        resp = client.get("/api/queue")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/queue")
        assert resp.status_code == 503


class TestQueueStats:
    """GET /api/queue/stats — requires DB + registry_read."""

    def test_requires_auth(self, client):
        resp = client.get("/api/queue/stats")
        assert resp.status_code == 401

    def test_returns_503_without_db(self, read_client):
        resp = read_client.get("/api/queue/stats")
        assert resp.status_code == 503


class TestQueueGenerate:
    """POST /api/queue/generate — requires DB + admin."""

    def test_requires_auth(self, client):
        resp = client.post("/api/queue/generate", json={"target_level": "L1_contact_confirmed"})
        assert resp.status_code == 401

    def test_requires_admin(self, read_client):
        resp = read_client.post("/api/queue/generate", json={"target_level": "L1_contact_confirmed"})
        assert resp.status_code == 403

    def test_returns_503_without_db(self, admin_client):
        resp = admin_client.post("/api/queue/generate", json={"target_level": "L1_contact_confirmed"})
        assert resp.status_code == 503


class TestQueueClaim:
    """POST /api/queue/{task_id}/claim — requires DB + registry_write."""

    def test_requires_auth(self, client):
        resp = client.post("/api/queue/some-task-id/claim")
        assert resp.status_code == 401

    def test_requires_write(self, read_client):
        resp = read_client.post("/api/queue/some-task-id/claim")
        assert resp.status_code == 403


class TestQueueComplete:
    """POST /api/queue/{task_id}/complete — requires DB + registry_write."""

    def test_requires_auth(self, client):
        resp = client.post(
            "/api/queue/some-task-id/complete",
            json={
                "target_level": "L1_contact_confirmed",
                "evidence_type": "contact_confirmation",
                "actor_id": "test",
                "actor_type": "human_verifier",
            },
        )
        assert resp.status_code == 401


class TestQueueSkip:
    """POST /api/queue/{task_id}/skip — requires DB + registry_write."""

    def test_requires_auth(self, client):
        resp = client.post(
            "/api/queue/some-task-id/skip",
            json={"reason": "Cannot reach pharmacy"},
        )
        assert resp.status_code == 401
