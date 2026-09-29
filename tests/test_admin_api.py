"""Admin endpoints stay locked and single-flighted."""

from unittest.mock import patch

import pytest

from app.services import admin_jobs


@pytest.fixture(autouse=True)
def release_lock():
    yield
    if admin_jobs._state["is_running"]:
        admin_jobs.finish()


class TestAuth:
    def test_disabled_when_no_key_configured(self, client):
        with patch.object(admin_jobs, "_state", admin_jobs._state):
            response = client.post("/api/admin/crawl")
        assert response.status_code == 503

    def test_wrong_key_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr("app.config.config.ADMIN_API_KEY", "secret")
        response = client.post(
            "/api/admin/crawl", headers={"X-Admin-Key": "wrong"}
        )
        assert response.status_code == 401

    def test_missing_header_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr("app.config.config.ADMIN_API_KEY", "secret")
        assert client.post("/api/admin/crawl").status_code == 401


class TestCrawlTrigger:
    def test_accepted_with_valid_key(self, client, monkeypatch):
        monkeypatch.setattr("app.config.config.ADMIN_API_KEY", "secret")
        with patch.object(admin_jobs, "run_crawl_job") as job:
            response = client.post(
                "/api/admin/crawl", headers={"X-Admin-Key": "secret"}
            )
        assert response.status_code == 202
        assert response.json()["job_type"] == "crawl"
        job.assert_called_once()

    def test_second_job_is_rejected_while_one_runs(self, client, monkeypatch):
        monkeypatch.setattr("app.config.config.ADMIN_API_KEY", "secret")
        assert admin_jobs.try_start("crawl") is True
        response = client.post(
            "/api/admin/crawl", headers={"X-Admin-Key": "secret"}
        )
        assert response.status_code == 409

    def test_unknown_source_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr("app.config.config.ADMIN_API_KEY", "secret")
        response = client.post(
            "/api/admin/crawl/nosuchbank", headers={"X-Admin-Key": "secret"}
        )
        assert response.status_code == 422


class TestStatus:
    def test_status_reports_idle_state(self, client, monkeypatch):
        monkeypatch.setattr("app.config.config.ADMIN_API_KEY", "secret")
        response = client.get(
            "/api/admin/status", headers={"X-Admin-Key": "secret"}
        )
        assert response.status_code == 200
        assert "is_running" in response.json()
