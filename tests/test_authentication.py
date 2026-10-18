from __future__ import annotations

from html import unescape
import logging
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from contextops.auth import classify_login_csrf_reason
from contextops.main import create_app
from tests.helpers import (
    authenticated_client,
    client_without_csrf,
    csrf_token,
)


def raw_client(tmp_path, *, base_url: str = "http://testserver") -> TestClient:
    return TestClient(
        create_app(str(tmp_path / "contextops.db")),
        base_url=base_url,
    )


def credentials() -> dict[str, str]:
    return {
        "username": os.environ["CONTEXTOPS_ADMIN_USERNAME"],
        "password": os.environ["CONTEXTOPS_ADMIN_PASSWORD"],
    }


def submit_login(
    client: TestClient,
    *,
    username: str | None = None,
    password: str | None = None,
    next_path: str | None = None,
    csrf: str | None = None,
) -> tuple[TestClient, object]:
    page = client.get("/login", params={"next": next_path} if next_path else None)
    assert page.status_code == 200
    token = csrf_token(page)
    form = {
        **credentials(),
        "username": username if username is not None else credentials()["username"],
        "password": password if password is not None else credentials()["password"],
        "csrf_token": token if csrf is None else csrf,
        "next": next_path or "/",
    }
    return client, client.post("/login", data=form, follow_redirects=False)


def redirect_path(response: object) -> str:
    location = response.headers["location"]  # type: ignore[attr-defined]
    return urlparse(location).path


def normalised_failure(response: object) -> str:
    """Compare the visible login failure without form values or CSRF tokens."""

    if response.headers.get("content-type", "").startswith("application/json"):  # type: ignore[attr-defined]
        return str(response.json())  # type: ignore[attr-defined]
    text = response.text  # type: ignore[attr-defined]
    text = re.sub(r"<input\b[^>]*>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<script\b.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(unescape(text).split())


def session_cookie_headers(response: object) -> list[str]:
    headers = response.headers.get_list("set-cookie")  # type: ignore[attr-defined]
    session_headers = [header for header in headers if "httponly" in header.lower()]
    assert session_headers, "successful login must set an HttpOnly session cookie"
    return session_headers


def test_login_page_and_valid_post_redirect_to_dashboard_with_session(tmp_path) -> None:
    client = raw_client(tmp_path)

    page = client.get("/login")

    assert page.status_code == 200
    assert re.search(r'name=["\']username["\']', page.text)
    assert re.search(r'name=["\']password["\']', page.text)
    assert re.search(r'name=["\']csrf_token["\']', page.text)

    _, login = submit_login(client)

    assert login.status_code in {302, 303, 307}
    assert redirect_path(login) in {"/", "/dashboard"}
    assert client.get(redirect_path(login)).status_code == 200
    assert client.get("/").status_code == 200


def test_wrong_username_and_wrong_password_have_the_same_unauthorized_failure(
    tmp_path,
) -> None:
    client = raw_client(tmp_path)

    _, wrong_username = submit_login(
        client,
        username="not-the-admin",
    )
    _, wrong_password = submit_login(
        client,
        password="not-the-password",
    )

    assert wrong_username.status_code == 401
    assert wrong_password.status_code == 401
    assert normalised_failure(wrong_username) == normalised_failure(wrong_password)


def test_repeated_failed_logins_are_throttled(tmp_path) -> None:
    client = raw_client(tmp_path)

    failures = [
        submit_login(client, password="not-the-password")[1]
        for _ in range(4)
    ]

    assert [response.status_code for response in failures[:3]] == [401, 401, 401]
    assert failures[3].status_code == 429
    assert int(failures[3].headers["retry-after"]) >= 1


def test_logout_invalidates_the_authenticated_session(tmp_path) -> None:
    client = authenticated_client(create_app(str(tmp_path / "contextops.db")))
    assert client.get("/").status_code == 200
    assert client.csrf_token

    logout = client.post(
        "/logout",
        data={"csrf_token": client.csrf_token},
        follow_redirects=False,
    )

    assert logout.status_code in {302, 303, 307}
    assert redirect_path(logout) == "/login"
    protected_page = client.get("/", follow_redirects=False)
    assert protected_page.status_code in {302, 303, 307}
    assert redirect_path(protected_page) == "/login"
    protected_api = client.get("/api/rules")
    assert protected_api.status_code == 401
    assert protected_api.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize(
    "path",
    ["/", "/rules", "/cases/new", "/cases/case-outside-hours"],
)
def test_protected_html_pages_redirect_unauthenticated_clients_to_login(path, tmp_path) -> None:
    response = raw_client(tmp_path).get(path, follow_redirects=False)

    assert response.status_code in {302, 303, 307}
    assert redirect_path(response) == "/login"
    assert parse_qs(urlparse(response.headers["location"]).query)["next"] == [path]


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/api/rules", {}),
        ("get", "/api/cases/case-outside-hours", {}),
        ("post", "/api/cases", {"json": {}}),
    ],
)
def test_protected_apis_return_json_401_to_unauthenticated_clients(
    method, path, kwargs, tmp_path
) -> None:
    response = getattr(raw_client(tmp_path), method)(path, **kwargs)

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert isinstance(response.json(), dict)


def test_health_remains_public_without_a_session(tmp_path) -> None:
    response = raw_client(tmp_path).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "contextops"}


@pytest.mark.parametrize("path", ["/docs", "/redoc"])
def test_documentation_pages_require_authentication(path, tmp_path) -> None:
    response = raw_client(tmp_path).get(path, follow_redirects=False)

    assert response.status_code in {302, 303, 307}
    assert redirect_path(response) == "/login"


def test_openapi_json_requires_authentication(tmp_path) -> None:
    response = raw_client(tmp_path).get("/openapi.json")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_authenticated_documentation_surfaces_remain_available(tmp_path) -> None:
    client = authenticated_client(create_app(str(tmp_path / "docs.db")))

    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_missing_admin_credentials_fail_app_creation(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CONTEXTOPS_ADMIN_USERNAME")
    monkeypatch.delenv("CONTEXTOPS_ADMIN_PASSWORD")

    with pytest.raises(RuntimeError, match="administrator credentials are not configured"):
        create_app(str(tmp_path / "missing-credentials.db"))


def test_unchanged_env_example_cannot_start_with_admin_credentials(
    tmp_path, monkeypatch
) -> None:
    example_values = {}
    example_path = Path(__file__).parents[1] / ".env.example"
    for line in example_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("CONTEXTOPS_ADMIN_"):
            name, value = line.split("=", 1)
            example_values[name] = value
            monkeypatch.setenv(name, value)

    assert example_values == {
        "CONTEXTOPS_ADMIN_USERNAME": "",
        "CONTEXTOPS_ADMIN_PASSWORD": "",
    }
    with pytest.raises(RuntimeError, match="administrator credentials are not configured"):
        create_app(str(tmp_path / "unchanged-example.db"))


def test_login_rejects_missing_and_invalid_csrf_tokens_with_403(tmp_path) -> None:
    client = raw_client(tmp_path)
    page = client.get("/login")
    assert page.status_code == 200
    token = csrf_token(page)

    missing = client.post(
        "/login",
        data={**credentials()},
        follow_redirects=False,
    )
    invalid = client.post(
        "/login",
        data={**credentials(), "csrf_token": f"{token}-invalid"},
        follow_redirects=False,
    )

    assert missing.status_code == 403
    assert invalid.status_code == 403


@pytest.mark.parametrize(
    ("diagnostic", "expected_reason"),
    [
        (
            {
                "form_token_present": False,
                "cookie_present": True,
                "tokens_equal": False,
                "challenge_present": True,
                "challenge_active": True,
                "challenge_expired": False,
            },
            "missing_form_token",
        ),
        (
            {
                "form_token_present": True,
                "cookie_present": False,
                "tokens_equal": False,
                "challenge_present": False,
                "challenge_active": False,
                "challenge_expired": False,
            },
            "missing_cookie",
        ),
        (
            {
                "form_token_present": True,
                "cookie_present": True,
                "tokens_equal": False,
                "challenge_present": True,
                "challenge_active": True,
                "challenge_expired": False,
            },
            "token_mismatch",
        ),
        (
            {
                "form_token_present": True,
                "cookie_present": True,
                "tokens_equal": True,
                "challenge_present": False,
                "challenge_active": False,
                "challenge_expired": False,
            },
            "challenge_missing",
        ),
        (
            {
                "form_token_present": True,
                "cookie_present": True,
                "tokens_equal": True,
                "challenge_present": True,
                "challenge_active": False,
                "challenge_expired": True,
            },
            "challenge_expired",
        ),
        (
            {
                "form_token_present": True,
                "cookie_present": True,
                "tokens_equal": True,
                "challenge_present": True,
                "challenge_active": True,
                "challenge_expired": False,
                "atomic_consumption_result": False,
            },
            "atomic_consumption_failed",
        ),
    ],
)
def test_login_csrf_diagnostic_reason_classification(diagnostic, expected_reason) -> None:
    assert classify_login_csrf_reason(**diagnostic) == expected_reason


def test_login_csrf_diagnostics_are_safe_and_rejections_remain_403(
    tmp_path, monkeypatch, caplog
) -> None:
    monkeypatch.setenv("CONTEXTOPS_CSRF_DIAGNOSTICS", "true")
    client = raw_client(tmp_path)
    page = client.get("/login")
    token = csrf_token(page)
    password = credentials()["password"]
    session_id = "session-id-must-not-be-logged"
    client.cookies.set("contextops_session", session_id)

    caplog.set_level(logging.WARNING, logger="contextops.main")
    missing = client.post(
        "/login",
        data={**credentials()},
        follow_redirects=False,
    )

    assert missing.status_code == 403
    missing_record = caplog.records[-1]
    assert missing_record.login_csrf_stage == "initial_validation"
    assert missing_record.login_csrf_reason == "missing_form_token"
    assert missing_record.form_token_present is False
    assert missing_record.cookie_present is True
    assert missing_record.tokens_equal is False
    assert missing_record.challenge_present is True
    assert missing_record.challenge_active is True
    assert missing_record.challenge_expired is False
    assert missing_record.atomic_consumption_result == "not_attempted"

    caplog.clear()
    invalid = client.post(
        "/login",
        data={**credentials(), "csrf_token": f"{token}-invalid"},
        follow_redirects=False,
    )

    assert invalid.status_code == 403
    invalid_record = caplog.records[-1]
    assert invalid_record.login_csrf_stage == "initial_validation"
    assert invalid_record.login_csrf_reason == "token_mismatch"
    assert invalid_record.form_token_present is True
    assert invalid_record.cookie_present is True
    assert invalid_record.tokens_equal is False
    assert invalid_record.challenge_present is True
    assert invalid_record.challenge_active is True
    assert invalid_record.challenge_expired is False
    assert invalid_record.atomic_consumption_result == "not_attempted"
    assert token not in caplog.text
    assert credentials()["username"] not in caplog.text
    assert password not in caplog.text
    assert session_id not in caplog.text


def test_login_csrf_diagnostics_are_disabled_by_default(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.delenv("CONTEXTOPS_CSRF_DIAGNOSTICS", raising=False)
    client = raw_client(tmp_path)

    caplog.set_level(logging.WARNING, logger="contextops.main")
    response = client.post(
        "/login",
        data={**credentials()},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert not [record for record in caplog.records if record.name == "contextops.main"]


def test_enabled_login_csrf_diagnostics_do_not_change_successful_login(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CONTEXTOPS_CSRF_DIAGNOSTICS", "true")
    client = raw_client(tmp_path)

    _, response = submit_login(client)

    assert response.status_code in {302, 303, 307}


def test_login_csrf_atomic_consumption_failure_is_rejected_and_logged(
    tmp_path, monkeypatch, caplog
) -> None:
    monkeypatch.setenv("CONTEXTOPS_CSRF_DIAGNOSTICS", "true")
    client = raw_client(tmp_path)
    authentication = client.app.state.authentication
    monkeypatch.setattr(authentication, "consume_login_csrf", lambda _cookie: False)
    page = client.get("/login")
    token = csrf_token(page)

    caplog.set_level(logging.WARNING, logger="contextops.main")
    response = client.post(
        "/login",
        data={**credentials(), "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 403
    record = caplog.records[-1]
    assert record.login_csrf_stage == "atomic_consumption"
    assert record.login_csrf_reason == "atomic_consumption_failed"
    assert record.atomic_consumption_result == "false"
    assert record.form_token_present is True
    assert record.cookie_present is True
    assert record.tokens_equal is True


def test_login_csrf_consumption_is_single_use_across_instances(tmp_path) -> None:
    database_path = str(tmp_path / "contextops.db")
    first = raw_client(tmp_path)
    second = TestClient(create_app(database_path), base_url="http://testserver")
    token = first.app.state.authentication.issue_login_csrf()

    assert first.app.state.authentication.validate_login_csrf(token, token)
    assert second.app.state.authentication.consume_login_csrf(token)
    assert not first.app.state.authentication.consume_login_csrf(token)


def test_failed_login_block_is_rechecked_before_incrementing(tmp_path) -> None:
    database_path = str(tmp_path / "contextops.db")
    first = raw_client(tmp_path)
    second = TestClient(create_app(database_path), base_url="http://testserver")
    client_ip = "shared-concurrency-test-client"

    assert first.app.state.authentication.record_failed_login(client_ip) == 0.0
    assert first.app.state.authentication.record_failed_login(client_ip) == 0.0
    assert first.app.state.authentication.record_failed_login(client_ip) == 0.0
    assert second.app.state.authentication.record_failed_login(client_ip) > 0.0


def test_login_csrf_and_session_survive_app_recreation(tmp_path) -> None:
    database_path = str(tmp_path / "contextops.db")
    first_instance = raw_client(tmp_path)

    login_page = first_instance.get("/login")
    token = csrf_token(login_page)

    second_instance = TestClient(
        create_app(database_path),
        base_url="http://testserver",
    )
    second_instance.cookies.update(first_instance.cookies)
    login = second_instance.post(
        "/login",
        data={**credentials(), "csrf_token": token},
        follow_redirects=False,
    )

    assert login.status_code in {302, 303, 307}

    restarted_instance = TestClient(
        create_app(database_path),
        base_url="http://testserver",
    )
    restarted_instance.cookies.update(second_instance.cookies)
    protected_page = restarted_instance.get("/")
    assert protected_page.status_code == 200
    session_csrf = csrf_token(protected_page)
    decision = restarted_instance.post(
        "/api/cases/case-outside-hours/decision",
        json={
            "decision": "reject",
            "rationale": "The proposal is outside the stated working hours.",
        },
        headers={"X-CSRF-Token": session_csrf},
    )
    assert decision.status_code == 200


def test_authenticated_state_changing_requests_reject_missing_and_invalid_csrf(
    tmp_path,
) -> None:
    authenticated = authenticated_client(create_app(str(tmp_path / "contextops.db")))
    client = client_without_csrf(authenticated)
    payload = {"decision": "reject", "rationale": "The proposal is outside working hours."}
    before = authenticated.get("/api/cases/case-outside-hours").json()

    missing = client.post(
        "/api/cases/case-outside-hours/decision",
        json=payload,
    )
    invalid = client.post(
        "/api/cases/case-outside-hours/decision",
        json=payload,
        headers={"X-CSRF-Token": "invalid-token"},
    )
    missing_logout = client.post("/logout")
    invalid_logout = client.post(
        "/logout",
        headers={"X-CSRF-Token": "invalid-token"},
    )

    assert missing.status_code == 403
    assert invalid.status_code == 403
    assert missing_logout.status_code == 403
    assert invalid_logout.status_code == 403
    after = authenticated.get("/api/cases/case-outside-hours").json()
    assert after["decisions"] == before["decisions"]


def test_safe_next_path_is_honoured_but_external_next_url_is_rejected(tmp_path) -> None:
    client = raw_client(tmp_path)

    _, local_login = submit_login(client, next_path="/rules")
    assert local_login.status_code in {302, 303, 307}
    assert redirect_path(local_login) == "/rules"
    assert client.get("/rules").status_code == 200

    _, external_login = submit_login(client, next_path="https://evil.example/account")

    assert external_login.status_code in {302, 303, 307}
    assert redirect_path(external_login) in {"/", "/dashboard"}
    assert "evil.example" not in external_login.headers["location"]


def test_session_cookie_is_httponly_and_samesite_lax(tmp_path) -> None:
    client = raw_client(tmp_path)
    _, login = submit_login(client)

    for header in session_cookie_headers(login):
        assert "httponly" in header.lower()
        assert re.search(r"samesite=lax(?:;|$)", header, flags=re.IGNORECASE)


def test_session_cookie_is_secure_when_secure_cookies_are_configured(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CONTEXTOPS_COOKIE_SECURE", "true")
    monkeypatch.setenv("APP_ENV", "production")

    client = raw_client(tmp_path, base_url="https://testserver")
    _, login = submit_login(client)

    assert any("secure" in header.lower() for header in session_cookie_headers(login))


def test_authenticated_review_workflow_remains_available(tmp_path) -> None:
    client = authenticated_client(create_app(str(tmp_path / "contextops.db")))

    page = client.get("/cases/case-outside-hours")
    decision = client.post(
        "/api/cases/case-outside-hours/decision",
        json={
            "decision": "reject",
            "rationale": "The proposed start is after the stated working-hours window.",
        },
    )

    assert page.status_code == 200
    assert "Record your decision" in page.text
    assert decision.status_code == 200
    assert decision.json()["decision"] == "reject"
