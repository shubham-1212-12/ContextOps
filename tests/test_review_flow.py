from fastapi.testclient import TestClient

from contextops.main import create_app


def client_for(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "contextops.db")))


def test_end_to_end_rejection_rule_activation_and_regression(tmp_path) -> None:
    client = client_for(tmp_path)

    page = client.get("/cases/case-outside-hours")
    assert page.status_code == 200
    assert "confirmed violation" in page.text
    assert "warning" in page.text
    assert "unresolved ambiguity" in page.text
    assert "18:00:00+05:30" in page.text

    decision = client.post(
        "/api/cases/case-outside-hours/decision",
        json={
            "decision": "reject",
            "rationale": "The proposed start is after the stated working-hours window.",
        },
    )
    assert decision.status_code == 200
    assert decision.json()["decision"] == "reject"
    assert decision.json()["preserved_original_proposal"] is True

    case_data = client.get("/api/cases/case-outside-hours").json()
    assert case_data["original_proposal"]["start"] == "2026-10-16T18:00:00+05:30"
    assert case_data["decisions"][0]["rationale"].startswith("The proposed start")
    assert any(event["event_type"] == "human_decision_recorded" for event in case_data["audit"])

    issue_id = next(
        issue["id"]
        for issue in case_data["issues"]
        if issue["code"] == "outside_working_hours"
    )
    draft = client.post(
        "/api/cases/case-outside-hours/rules", json={"issue_id": issue_id}
    )
    assert draft.status_code == 200
    assert draft.json()["status"] == "draft"
    assert draft.json()["evaluation_result"] is None
    assert {
        "title",
        "description",
        "failure_pattern",
        "triggering_condition",
        "expected_behavior",
        "test_case",
        "evaluation_result",
        "status",
    }.issubset(draft.json())

    not_yet_evaluated = client.post(
        "/api/cases/case-similar-outside-hours/evaluate-rules"
    )
    assert not_yet_evaluated.status_code == 200
    assert not_yet_evaluated.json()["evaluations"] == []

    rule_id = draft.json()["id"]
    activated = client.post(f"/api/rules/{rule_id}/activate")
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"

    regression = client.post(
        "/api/cases/case-similar-outside-hours/evaluate-rules"
    )
    assert regression.status_code == 200
    assert regression.json()["evaluations"] == [
        {
            "rule_id": rule_id,
            "case_id": "case-similar-outside-hours",
            "rule_title": "Prevent: Proposal is outside stated working hours",
            "result": "matched",
        }
    ]

    similar_case = client.get("/api/cases/case-similar-outside-hours").json()
    assert similar_case["evaluations"][0]["result"] == "matched"
    assert any(
        event["event_type"] == "quality_rule_evaluated"
        for event in similar_case["audit"]
    )

    disabled = client.post(f"/api/rules/{rule_id}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert client.post(
        "/api/cases/case-similar-outside-hours/evaluate-rules"
    ).json()["evaluations"] == []
