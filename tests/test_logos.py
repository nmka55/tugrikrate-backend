"""Source logos: provenance on disk, and delivery through the feed."""

import hashlib
import json
import struct
from unittest.mock import MagicMock, patch

import pytest

from app.config import config
from app.sources.logos import LOGO_DIR, MANIFEST_PATH, load_manifest
from app.sources.registry import BY_ID
from scripts import fetch_logos

# Deliberately without a logo: see the note in scripts/fetch_logos.py.
NO_LOGO = {"naimansharga"}


class TestManifest:
    def test_every_source_but_the_documented_exception_has_a_logo(self):
        assert set(BY_ID) - set(load_manifest()) == NO_LOGO

    def test_no_logo_is_recorded_for_an_unknown_source(self):
        assert set(load_manifest()) <= set(BY_ID)

    @pytest.mark.parametrize("source_id", sorted(load_manifest()))
    def test_file_matches_its_recorded_hash_and_size(self, source_id):
        entry = load_manifest()[source_id]
        data = (LOGO_DIR / entry["file"]).read_bytes()

        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        assert len(data) == entry["bytes"]
        content_type, width, height = fetch_logos.image_info(data)
        assert (content_type, width, height) == (
            entry["content_type"],
            entry["width"],
            entry["height"],
        )

    @pytest.mark.parametrize("source_id", sorted(load_manifest()))
    def test_every_logo_records_where_it_came_from(self, source_id):
        entry = load_manifest()[source_id]
        assert entry["origin_url"].startswith("https://")
        assert entry["origin_page"].startswith("https://")
        assert entry["publisher"]
        assert entry["origin_kind"] in {"appstore", "url"}

    def test_logos_are_formats_an_ios_image_view_can_load(self):
        # UIImage cannot load SVG from a URL, so only raster formats.
        for entry in load_manifest().values():
            assert entry["content_type"] in {"image/png", "image/jpeg"}
            assert min(entry["width"], entry["height"]) >= 96

    def test_manifest_is_valid_json_at_the_documented_path(self):
        assert "logos" in json.loads(MANIFEST_PATH.read_text("utf-8"))


class TestFeed:
    def source(self, client, source_id, **kwargs):
        body = client.get("/v1/rates", **kwargs).json()
        return next(s for s in body["sources"] if s["id"] == source_id)

    def test_logo_url_is_absolute_and_carries_a_content_hash(self, client):
        url = self.source(client, "khanbank")["logo_url"]
        digest = load_manifest()["khanbank"]["sha256"][:12]
        assert url == (
            f"http://testserver/static/logos/khanbank.jpg?v={digest}"
        )

    def test_a_source_without_a_logo_says_null(self, client):
        assert self.source(client, "naimansharga")["logo_url"] is None

    def test_public_base_url_wins_over_the_request_origin(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            config, "PUBLIC_BASE_URL", "https://api.example.com"
        )
        url = self.source(client, "khanbank")["logo_url"]
        assert url.startswith("https://api.example.com/static/logos/")

    def test_the_url_actually_serves_the_image(self, client):
        url = self.source(client, "mongolbank")["logo_url"]
        response = client.get(url.removeprefix("http://testserver"))

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert "max-age=604800" in response.headers["cache-control"]

    def test_missing_logo_file_is_a_404_not_a_crash(self, client):
        assert client.get("/static/logos/nope.png").status_code == 404

    def test_etag_does_not_depend_on_the_host_the_app_used(
        self, client, monkeypatch
    ):
        first = client.get("/v1/rates").headers["etag"]
        other = client.get(
            "/v1/rates", headers={"host": "192.168.0.143:8000"}
        ).headers["etag"]
        assert first == other

    def test_sources_endpoint_carries_logo_urls_too(self, client):
        sources = client.get("/v1/sources").json()["sources"]
        khan = next(s for s in sources if s["id"] == "khanbank")
        assert khan["logo_url"].startswith("http://testserver/static/logos/")

    def test_static_files_do_not_spend_the_api_rate_limit(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(config, "RATE_LIMIT_REQUESTS", 2)
        path = "/static/logos/khanbank.jpg"
        assert all(client.get(path).status_code == 200 for _ in range(6))
        assert client.get("/v1/rates").status_code == 200


def png(width, height):
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\0\0\0\rIHDR"
        + struct.pack(">II", width, height)
    )


class TestImageInfo:
    def test_png(self):
        assert fetch_logos.image_info(png(512, 256)) == (
            "image/png",
            512,
            256,
        )

    def test_gif(self):
        gif = b"GIF89a" + struct.pack("<HH", 120, 60)
        assert fetch_logos.image_info(gif) == ("image/gif", 120, 60)

    def test_jpeg(self):
        jpeg = (
            b"\xff\xd8\xff\xe0\x00\x04\x00\x00"
            b"\xff\xc0\x00\x0b\x08" + struct.pack(">HH", 300, 400)
        )
        assert fetch_logos.image_info(jpeg) == ("image/jpeg", 400, 300)

    def test_an_html_error_page_served_with_200_is_rejected(self):
        with pytest.raises(ValueError):
            fetch_logos.image_info(b"<!doctype html><title>Access Denied")


class TestPublisherCheck:
    @patch("scripts.fetch_logos.time.sleep")
    @patch("scripts.fetch_logos.requests.get")
    def test_an_app_id_taken_over_by_someone_else_fails_loudly(
        self, mock_get, _sleep
    ):
        response = MagicMock()
        response.json.return_value = {
            "results": [{"sellerName": "Some Other LLC", "artworkUrl512": "x"}]
        }
        mock_get.return_value = response

        with pytest.raises(ValueError, match="Some Other LLC"):
            fetch_logos._appstore_url(1, "Khan Bank")
