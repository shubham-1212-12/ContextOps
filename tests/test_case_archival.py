from __future__ import annotations

from fastapi.testclient import TestClient

from contextops.main import create_app
from tests.helpers import authenticated_client, client_without_csrf


def payload(title: str) -> dict:
    return {
        "title": title,
        "original_proposal": {
            "event": title,
            "start": "2026-10-20T18:00:00+05:30",
            "duration_minutes": 45,
            "timezone": "Asia/Kolkata",
        },
        "context": {
            "timezone": "Asia/Kolkata",
            "working_hours": {"start": "09:00", "end": "17:00"},
            "calendar_data": {"status": "not_provided"},
            "constraints": ["Keep the review within working hours."],
            "blocked_time": [],
        },
    }


def client_for(tmp_path) -> TestClient:
    return authenticated_client(create_app(str(tmp_path / "contextops.db")))


def create_case(client: TestClient, title: str) -> dict:
    response = client.post("/api/cases", json=payload(title))
    assert response.status_code == 201
    return response.json()["case"]


def test_case_page_exposes_explicit_delete_confirmation_action(tmp_path) -> None:
    client = client_for(tmp_path)
    case = create_case(client, "Archive confirmation case")

    page = client.get(f"/cases/{case['id']}")
    script = client.get("/static/app.js")

    assert page.status_code == 200
    assert 'data-action="archive-case"' in page.text
    assert "Delete Case" in page.text
    assert script.status_code == 200
    assert 'action === "archive-case"' in script.text
    assert "window.confirm" in script.text


def test_archive_requires_authentication_and_csrf(tmp_path) -> None:
    client = client_for(tmp_path)
    case = create_case(client, "Archive authorization case")

    unauthenticated = TestClient(client.app, base_url=str(client.base_url))
    assert unauthenticated.post(f"/api/cases/{case['id']}/archive").status_code == 401

    without_csrf = client_without_csrf(client)
    response = without_csrf.post(f"/api/cases/{case['id']}/archive")

    assert response.status_code == 403
    assert client.get(f"/api/cases/{case['id']}").json()["archived_at"] is None


def test_archive_preserves_case_history_and_quality_rule_references(tmp_path) -> None:
    client = client_for(tmp_path)
    source = create_case(client, "Archive source case")
    target = create_case(client, "Archive target case")

    decision = client.post(
        f"/api/cases/{source['id']}/decision",
        json={"decision": "reject", "rationale": "Archive test decision."},
    )
    assert decision.status_code == 200
    source_data = client.get(f"/api/cases/{source['id']}").json()
    issue_id = next(
        issue["id"]
        for issue in source_data["issues"]
        if issue["category"] == "confirmed_violation"
    )
    rule = client.post(
        f"/api/cases/{source['id']}/rules", json={"issue_id": issue_id}
    )
    assert rule.status_code == 200
    rule_id = rule.json()["id"]
    assert client.post(f"/api/rules/{rule_id}/activate").status_code == 200

    archived = client.post(f"/api/cases/{source['id']}/archive")

    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert archived.json()["redirect"] == "/?archived=1"
    assert source["title"] not in client.get("/").text
    assert "Case deleted from the review queue" in client.get("/?archived=1").text

    archived_case = client.get(f"/api/cases/{source['id']}").json()
    assert archived_case["archived_at"]
    assert archived_case["decisions"][0]["rationale"] == "Archive test decision."
    assert archived_case["audit"][-1]["event_type"] == "case_archived"
    archived_page = client.get(f"/cases/{source['id']}")
    assert "This case is archived" in archived_page.text
    assert 'data-action="archive-case"' not in archived_page.text

    stored_rule = next(
        item for item in client.get("/api/rules").json() if item["id"] == rule_id
    )
    assert stored_rule["status"] == "active"
    assert stored_rule["source_case_id"] == source["id"]
    assert stored_rule["source_case_title"] == source["title"]

    evaluated = client.post(f"/api/cases/{target['id']}/evaluate-rules")
    assert evaluated.status_code == 200
    assert evaluated.json()["evaluations"][0]["rule_id"] == rule_id
    assert client.get("/api/cases/case-outside-hours").status_code == 200
    assert client.get("/api/cases/case-similar-outside-hours").status_code == 200


def test_archive_is_atomic_for_missing_cases_and_idempotent_for_archived_cases(
    tmp_path,
) -> None:
    client = client_for(tmp_path)
    missing = client.post("/api/cases/case-does-not-exist/archive")

    assert missing.status_code == 404

    case = create_case(client, "Archive idempotency case")
    first = client.post(f"/api/cases/{case['id']}/archive")
    second = client.post(f"/api/cases/{case['id']}/archive")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["archived_at"] == first.json()["archived_at"]
    archived_case = client.get(f"/api/cases/{case['id']}").json()
    assert [event["event_type"] for event in archived_case["audit"]].count(
        "case_archived"
    ) == 1
