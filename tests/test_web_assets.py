from __future__ import annotations

from fastapi.testclient import TestClient

from contextops.main import create_app


def test_login_references_same_origin_static_assets_and_assets_are_served(tmp_path) -> None:
    client = TestClient(create_app(str(tmp_path / "contextops.db")))

    page = client.get("/login")
    stylesheet = client.get("/static/style.css")
    script = client.get("/static/app.js")

    assert page.status_code == 200
    assert 'href="/static/style.css"' in page.text
    assert '<script src="/static/app.js"></script>' in page.text
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert "color-scheme: dark" in stylesheet.text
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert "postJson" in script.text
    assert 'action === "archive-case"' in script.text
    assert "window.confirm" in script.text
