from fastapi.testclient import TestClient

from contextops.main import create_app


def client_for(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "contextops.db")))


def payload() -> dict:
    return {
        "title": "Manual roadmap review",
        "original_proposal": {
            "event": "Manual roadmap review",
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


def test_new_case_page_and_dashboard_button_are_available(tmp_path) -> None:
    client = client_for(tmp_path)

    page = client.get("/cases/new")
    dashboard = client.get("/")

    assert page.status_code == 200
    assert "Submit a scheduling case" in page.text
    assert dashboard.status_code == 200
    assert 'href="/cases/new"' in dashboard.text
    assert "New case" in dashboard.text


def test_api_creation_returns_case_for_existing_review_workflow(tmp_path) -> None:
    client = client_for(tmp_path)

    created = client.post("/api/cases", json=payload())

    assert created.status_code == 201
    case_id = created.json()["case_id"]
    assert case_id.startswith("case-")
    assert created.json()["case"]["issues"]
    assert client.get(f"/cases/{case_id}").status_code == 200
    assert client.get(f"/api/cases/{case_id}").json()["title"] == "Manual roadmap review"

    dashboard = client.get("/")
    assert "Manual roadmap review" in dashboard.text
    assert f"/cases/{case_id}" in dashboard.text


def test_api_creation_rejects_malformed_input_without_creating_case(tmp_path) -> None:
    client = client_for(tmp_path)
    malformed = payload()
    del malformed["original_proposal"]["start"]

    response = client.post("/api/cases", json=malformed)

    assert response.status_code == 400
    assert "original_proposal.start" in response.json()["detail"]
    assert "Manual roadmap review" not in client.get("/").text


def test_seeded_demo_cases_remain_available_after_api_creation(tmp_path) -> None:
    client = client_for(tmp_path)

    before = client.get("/api/cases/case-outside-hours")
    created = client.post("/api/cases", json=payload())
    after = client.get("/api/cases/case-outside-hours")

    assert before.status_code == 200
    assert created.status_code == 201
    assert after.status_code == 200
    assert after.json()["original_proposal"]["event"] == "Quarterly design review"
    assert client.get("/api/cases/case-similar-outside-hours").status_code == 200
