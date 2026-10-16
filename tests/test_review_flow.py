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
    source_case = client.get("/api/cases/case-outside-hours").json()
    assert [event["event_type"] for event in source_case["audit"]] == [
        "human_decision_recorded",
        "quality_rule_drafted",
        "quality_rule_activated",
        "quality_rule_disabled",
    ]


def test_edit_requires_an_edited_proposal(tmp_path) -> None:
    client = client_for(tmp_path)

    response = client.post(
        "/api/cases/case-outside-hours/decision",
        json={"decision": "edit", "rationale": "A revised time is needed."},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "edited_proposal is required when decision is edit"


def test_disabled_rule_is_excluded_from_fresh_results_and_stops_new_audit_events(
    tmp_path,
) -> None:
    client = client_for(tmp_path)
    case_id = "case-outside-hours"
    regression_case_id = "case-similar-outside-hours"
    case_data = client.get(f"/api/cases/{case_id}").json()
    issue_id = next(
        issue["id"]
        for issue in case_data["issues"]
        if issue["code"] == "outside_working_hours"
    )
    rule = client.post(f"/api/cases/{case_id}/rules", json={"issue_id": issue_id}).json()
    rule_id = rule["id"]
    client.post(f"/api/rules/{rule_id}/activate")

    assert client.post(f"/api/cases/{regression_case_id}/evaluate-rules").json() == {
        "evaluations": [
            {
                "rule_id": rule_id,
                "case_id": regression_case_id,
                "rule_title": "Prevent: Proposal is outside stated working hours",
                "result": "matched",
            }
        ]
    }
    assert client.post(f"/api/cases/{regression_case_id}/evaluate-rules").json()[
        "evaluations"
    ][0]["result"] == "matched"

    active_case = client.get(f"/api/cases/{regression_case_id}").json()
    assert len(active_case["evaluations"]) == 1
    evaluation_event_count = sum(
        event["event_type"] == "quality_rule_evaluated"
        for event in active_case["audit"]
    )
    assert evaluation_event_count == 2

    disabled = client.post(f"/api/rules/{rule_id}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    for _ in range(3):
        assert client.post(
            f"/api/cases/{regression_case_id}/evaluate-rules"
        ).json() == {"evaluations": []}

    disabled_case = client.get(f"/api/cases/{regression_case_id}").json()
    assert disabled_case["evaluations"] == []
    assert sum(
        event["event_type"] == "quality_rule_evaluated"
        for event in disabled_case["audit"]
    ) == evaluation_event_count
    page = client.get(f"/cases/{regression_case_id}")
    assert page.status_code == 200
    assert "matched</strong>" not in page.text
    assert "No current active-rule results for this case." in page.text


def test_disabled_rule_can_be_reactivated_from_home_page(tmp_path) -> None:
    client = client_for(tmp_path)
    case_id = "case-outside-hours"
    case_data = client.get(f"/api/cases/{case_id}").json()
    issue_id = next(
        issue["id"]
        for issue in case_data["issues"]
        if issue["code"] == "outside_working_hours"
    )
    rule = client.post(f"/api/cases/{case_id}/rules", json={"issue_id": issue_id}).json()
    rule_id = rule["id"]
    client.post(f"/api/rules/{rule_id}/activate")
    disabled = client.post(f"/api/rules/{rule_id}/disable")
    assert disabled.json()["status"] == "disabled"

    page = client.get("/rules")
    assert page.status_code == 200
    assert f'data-action="activate-rule" data-rule-id="{rule_id}"' in page.text
    assert "Re-activate explicitly" in page.text

    reactivated = client.post(f"/api/rules/{rule_id}/activate")
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "active"


def test_dashboard_prioritizes_review_queue_and_separates_quality_rules(tmp_path) -> None:
    client = client_for(tmp_path)

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Review queue" in dashboard.text
    assert "Review case" in dashboard.text
    assert "Quarterly design review proposed after hours" in dashboard.text
    assert "1 confirmed" in dashboard.text
    assert "1 warning" in dashboard.text
    assert "1 unresolved" in dashboard.text
    assert 'href="/rules"' in dashboard.text
    assert "Failure pattern" not in dashboard.text

    rules_page = client.get("/rules")
    assert rules_page.status_code == 200
    assert "Quality rules" in rules_page.text


def test_queue_navigation_and_both_case_pages_keep_details_secondary(tmp_path) -> None:
    client = client_for(tmp_path)

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert dashboard.text.count('href="/rules"') == 1
    assert 'href="/">Review queue</a>' in dashboard.text
    assert client.get("/").status_code == 200

    for case_id in ("case-outside-hours", "case-similar-outside-hours"):
        case_page = client.get(f"/cases/{case_id}")
        assert case_page.status_code == 200
        assert 'class="back-link" href="/"' in case_page.text
        assert "Original scheduling proposal" in case_page.text
        assert "Contextual findings" in case_page.text
        assert "Record your decision" in case_page.text
        assert "View raw proposal JSON" in case_page.text
        assert "View supplied context" in case_page.text
        assert "Current active-rule results" in case_page.text
        assert "Audit history" in case_page.text
        assert "<details" in case_page.text
        assert client.get("/").status_code == 200


def test_legacy_accept_decision_remains_supported(tmp_path) -> None:
    client = client_for(tmp_path)

    response = client.post(
        "/api/cases/case-outside-hours/decision",
        json={"decision": "accept", "rationale": "Keep compatibility with existing clients."},
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "accept"


def test_all_human_decisions_persist_rationales_and_edit_without_losing_original(
    tmp_path,
) -> None:
    client = client_for(tmp_path)
    decisions = [
        ("approve", "The evidence supports this proposal."),
        (
            "edit",
            "Move the review into the confirmed working-hours window.",
        ),
        ("defer", "Wait for the missing attendee calendar evidence."),
        ("escalate", "A second reviewer should resolve the remaining ambiguity."),
    ]
    edited_proposal = {
        "event": "Quarterly design review",
        "start": "2026-10-16T16:00:00+05:30",
        "duration_minutes": 60,
        "timezone": "Asia/Kolkata",
    }

    for decision, rationale in decisions:
        response = client.post(
            "/api/cases/case-outside-hours/decision",
            json={
                "decision": decision,
                "rationale": rationale,
                "edited_proposal": edited_proposal if decision == "edit" else None,
            },
        )
        assert response.status_code == 200
        assert response.json()["decision"] == decision

    case_data = client.get("/api/cases/case-outside-hours").json()
    assert [decision["decision"] for decision in case_data["decisions"]] == [
        "approve",
        "edit",
        "defer",
        "escalate",
    ]
    assert [decision["rationale"] for decision in case_data["decisions"]] == [
        rationale for _, rationale in decisions
    ]
    assert case_data["decisions"][1]["edited_proposal"] == edited_proposal
    assert case_data["original_proposal"]["start"] == "2026-10-16T18:00:00+05:30"
    assert [event["event_type"] for event in case_data["audit"]] == [
        "human_decision_recorded",
        "human_decision_recorded",
        "human_decision_recorded",
        "human_decision_recorded",
    ]
    assert case_data["audit"][1]["payload"]["edited_proposal"] == edited_proposal
