"""Reusable TestClient helpers for authenticated regression tests."""

from __future__ import annotations

from html.parser import HTMLParser
import os
from typing import Any

from fastapi.testclient import TestClient


class _CsrfInputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.token: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        attributes = dict(attrs)
        if attributes.get("name") == "csrf_token":
            self.token = attributes.get("value")


def csrf_token(response: Any) -> str:
    """Extract the form CSRF token exposed by the login page."""

    parser = _CsrfInputParser()
    parser.feed(response.text)
    assert parser.token, "login page must expose a csrf_token hidden input"
    return parser.token


class AuthenticatedTestClient(TestClient):
    """TestClient that protects existing mutation tests from CSRF drift."""

    csrf_token: str | None = None
    auto_csrf = True

    def request(self, method: str, url: Any, **kwargs: Any) -> Any:
        if (
            self.auto_csrf
            and method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
            and self.csrf_token
        ):
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("X-CSRF-Token", self.csrf_token)
            kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


def authenticated_client(app: Any, *, base_url: str = "http://testserver") -> AuthenticatedTestClient:
    """Create a client and establish the configured test-admin session."""

    client = AuthenticatedTestClient(app, base_url=base_url)
    login_page = client.get("/login")
    assert login_page.status_code == 200
    token = csrf_token(login_page)
    client.csrf_token = token
    login = client.post(
        "/login",
        data={
            "username": os.environ["CONTEXTOPS_ADMIN_USERNAME"],
            "password": os.environ["CONTEXTOPS_ADMIN_PASSWORD"],
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert login.status_code in {302, 303, 307}
    protected_page = client.get("/")
    assert protected_page.status_code == 200
    client.csrf_token = csrf_token(protected_page)
    return client


def client_without_csrf(client: TestClient) -> TestClient:
    """Copy an authenticated cookie jar into a plain client for rejection tests."""

    raw = TestClient(client.app, base_url=str(client.base_url))
    raw.cookies.update(client.cookies)
    return raw
