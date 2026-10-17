from __future__ import annotations

from contextops.database import Database
from contextops.service import ContextOpsService


def make_service(tmp_path) -> ContextOpsService:
    return ContextOpsService(Database(str(tmp_path / "contextops.db")))


def issue_id_for(service: ContextOpsService, case_id: str, code: str) -> str:
    case = service.get_case(case_id)
    assert case is not None
    return next(issue["id"] for issue in case["issues"] if issue["code"] == code)


def test_rule_provenance_is_present_for_created_rule_and_rule_queries(tmp_path) -> None:
    service = make_service(tmp_path)
    case_id = "case-outside-hours"
    issue_id = issue_id_for(service, case_id, "outside_working_hours")

    created = service.create_rule_from_issue(case_id, issue_id)

    assert created["created"] is True
    assert created["duplicate"] is False
    assert created["source_issue_id"] == issue_id
    assert created["source_case_id"] == case_id
    assert created["source_case_title"] == "Quarterly design review proposed after hours"
    assert service.get_rule(created["id"]) == {
        key: value for key, value in created.items() if key not in {"created", "duplicate"}
    }
    listed = service.list_rules()
    assert listed == [
        {key: value for key, value in created.items() if key not in {"created", "duplicate"}}
    ]


def test_duplicate_from_another_case_reuses_active_rule_without_history_changes(tmp_path) -> None:
    service = make_service(tmp_path)
    source_case_id = "case-outside-hours"
    other_case_id = "case-similar-outside-hours"
    source_issue_id = issue_id_for(service, source_case_id, "outside_working_hours")
    other_issue_id = issue_id_for(service, other_case_id, "outside_working_hours")

    created = service.create_rule_from_issue(source_case_id, source_issue_id)
    active = service.activate_rule(created["id"])
    before_rules = service.list_rules()
    before_audits = {
        case_id: service.get_case(case_id)["audit"]
        for case_id in (source_case_id, other_case_id)
    }

    duplicate = service.create_rule_from_issue(other_case_id, other_issue_id)

    assert duplicate["id"] == active["id"]
    assert duplicate["status"] == "active"
    assert duplicate["duplicate"] is True
    assert duplicate["created"] is False
    assert duplicate["duplicate_status"] == "active"
    assert "equivalent quality rule already exists" in duplicate["duplicate_message"]
    assert duplicate["source_case_id"] == source_case_id
    assert duplicate["source_issue_id"] == source_issue_id
    assert service.list_rules() == before_rules
    assert service.get_case(source_case_id)["audit"] == before_audits[source_case_id]
    assert service.get_case(other_case_id)["audit"] == before_audits[other_case_id]


def test_duplicate_reuses_existing_draft(tmp_path) -> None:
    service = make_service(tmp_path)
    source_case_id = "case-outside-hours"
    other_case_id = "case-similar-outside-hours"
    source_issue_id = issue_id_for(service, source_case_id, "outside_working_hours")
    other_issue_id = issue_id_for(service, other_case_id, "outside_working_hours")

    draft = service.create_rule_from_issue(source_case_id, source_issue_id)
    duplicate = service.create_rule_from_issue(other_case_id, other_issue_id)

    assert duplicate["id"] == draft["id"]
    assert duplicate["status"] == "draft"
    assert duplicate["duplicate_status"] == "draft"
    assert duplicate["duplicate"] is True
    assert duplicate["created"] is False


def test_duplicate_reuses_disabled_rule_without_reactivating_or_mutating_history(tmp_path) -> None:
    service = make_service(tmp_path)
    source_case_id = "case-outside-hours"
    other_case_id = "case-similar-outside-hours"
    source_issue_id = issue_id_for(service, source_case_id, "outside_working_hours")
    other_issue_id = issue_id_for(service, other_case_id, "outside_working_hours")

    created = service.create_rule_from_issue(source_case_id, source_issue_id)
    disabled = service.disable_rule(created["id"])
    before_rules = service.list_rules()
    before_audit = service.get_case(other_case_id)["audit"]

    duplicate = service.create_rule_from_issue(other_case_id, other_issue_id)

    assert duplicate["id"] == disabled["id"]
    assert duplicate["status"] == "disabled"
    assert duplicate["duplicate_status"] == "disabled"
    assert duplicate["duplicate"] is True
    assert duplicate["created"] is False
    assert service.get_rule(created["id"])["status"] == "disabled"
    assert service.list_rules() == before_rules
    assert service.get_case(other_case_id)["audit"] == before_audit


def test_status_priority_selects_active_before_draft_and_disabled_then_lowest_id(
    tmp_path,
) -> None:
    service = make_service(tmp_path)
    source_case_id = "case-outside-hours"
    other_case_id = "case-similar-outside-hours"
    source_issue_id = issue_id_for(service, source_case_id, "outside_working_hours")
    other_issue_id = issue_id_for(service, other_case_id, "outside_working_hours")
    first = service.create_rule_from_issue(source_case_id, source_issue_id)

    with service.database.session() as connection:
        connection.execute(
            """
            INSERT INTO quality_rules
                (source_issue_id, title, description, failure_pattern,
                 triggering_condition, expected_behavior, test_case,
                 evaluation_result, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_issue_id,
                "Later active equivalent",
                "Equivalent rule",
                first["failure_pattern"],
                first["triggering_condition"],
                first["expected_behavior"],
                first["test_case"],
                None,
                "active",
            ),
        )
        active_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.execute(
            "UPDATE quality_rules SET status = 'disabled' WHERE id = ?", (first["id"],)
        )
        connection.execute(
            """
            INSERT INTO quality_rules
                (source_issue_id, title, description, failure_pattern,
                 triggering_condition, expected_behavior, test_case,
                 evaluation_result, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_issue_id,
                "Lowest disabled equivalent",
                "Equivalent rule",
                first["failure_pattern"],
                first["triggering_condition"],
                first["expected_behavior"],
                first["test_case"],
                None,
                "disabled",
            ),
        )

    duplicate = service.create_rule_from_issue(other_case_id, other_issue_id)

    assert duplicate["id"] == active_id
    assert duplicate["status"] == "active"
    assert service.get_rule(first["id"])["status"] == "disabled"


def test_different_definition_creates_distinct_rule_and_seeded_cases_stay_intact(tmp_path) -> None:
    service = make_service(tmp_path)
    source_case_id = "case-outside-hours"
    source_issue_id = issue_id_for(service, source_case_id, "outside_working_hours")
    warning_issue_id = issue_id_for(service, source_case_id, "offline_calendar_warning")
    other_case_before = service.get_case("case-similar-outside-hours")
    assert other_case_before is not None

    first = service.create_rule_from_issue(source_case_id, source_issue_id)
    distinct = service.create_rule_from_issue(source_case_id, warning_issue_id)

    assert distinct["created"] is True
    assert distinct["duplicate"] is False
    assert distinct["id"] != first["id"]
    assert distinct["failure_pattern"] != first["failure_pattern"]
    assert len(service.list_rules()) == 2
    assert service.get_case("case-similar-outside-hours") == other_case_before


def test_duplicate_rule_api_response_and_rules_page_explain_status_and_source_link(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from contextops.main import create_app

    client = TestClient(create_app(str(tmp_path / "api.db")))
    source_case_id = "case-outside-hours"
    other_case_id = "case-similar-outside-hours"
    source_issue_id = next(
        issue["id"]
        for issue in client.get(f"/api/cases/{source_case_id}").json()["issues"]
        if issue["code"] == "outside_working_hours"
    )
    other_issue_id = next(
        issue["id"]
        for issue in client.get(f"/api/cases/{other_case_id}").json()["issues"]
        if issue["code"] == "outside_working_hours"
    )

    created = client.post(
        f"/api/cases/{source_case_id}/rules", json={"issue_id": source_issue_id}
    ).json()
    client.post(f"/api/rules/{created['id']}/disable")
    duplicate = client.post(
        f"/api/cases/{other_case_id}/rules", json={"issue_id": other_issue_id}
    )

    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert duplicate.json()["created"] is False
    assert duplicate.json()["duplicate_status"] == "disabled"
    assert duplicate.json()["source_case_id"] == source_case_id

    page = client.get(f"/rules?duplicate_rule_id={created['id']}#rule-{created['id']}")
    assert page.status_code == 200
    assert "Equivalent rule already exists." in page.text
    assert "is <strong>disabled</strong>" in page.text
    assert f'href="#rule-{created["id"]}"' in page.text
    assert f'id="rule-{created["id"]}"' in page.text
    assert f'href="/cases/{source_case_id}"' in page.text
    assert "Quarterly design review proposed after hours" in page.text
