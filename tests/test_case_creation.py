from __future__ import annotations

import pytest

from contextops.database import Database
from contextops.service import ContextOpsService


def make_service(tmp_path) -> ContextOpsService:
    return ContextOpsService(Database(str(tmp_path / "contextops.db")))


def make_payload() -> tuple[str, dict, dict]:
    return (
        "New planning session",
        {
            "event": "Planning session",
            "start": "2026-10-16T18:00:00+05:30",
            "duration_minutes": 60,
            "timezone": "Asia/Kolkata",
        },
        {
            "timezone": "Asia/Kolkata",
            "working_hours": {"start": "09:00", "end": "17:00"},
            "calendar_data": {"status": "not_provided"},
            "blocked_time": [],
            "constraints": ["Stay within working hours."],
            "availability": [],
        },
    )


def test_create_case_persists_valid_payload_detected_issues_and_audit(tmp_path) -> None:
    service = make_service(tmp_path)
    title, proposal, context = make_payload()

    case = service.create_case(title, proposal, context)

    assert case["id"].startswith("case-")
    assert case["title"] == title
    assert case["original_proposal"] == proposal
    assert case["context"] == context
    assert {issue["code"] for issue in case["issues"]} >= {
        "outside_working_hours",
        "offline_calendar_warning",
        "attendee_availability_unresolved",
    }
    created_events = [event for event in case["audit"] if event["event_type"] == "case_created"]
    assert len(created_events) == 1
    assert created_events[0]["payload"] == {
        "case_id": case["id"],
        "title": title,
        "issue_count": len(case["issues"]),
    }


def test_record_decision_edit_preserves_original_proposal_for_created_case(tmp_path) -> None:
    service = make_service(tmp_path)
    title, proposal, context = make_payload()
    case = service.create_case(title, proposal, context)
    edited_proposal = {
        **proposal,
        "start": "2026-10-16T16:00:00+05:30",
    }

    decision = service.record_decision(
        case["id"],
        "edit",
        "Move the meeting into working hours.",
        edited_proposal,
    )

    assert decision["preserved_original_proposal"] is True
    stored = service.get_case(case["id"])
    assert stored is not None
    assert stored["original_proposal"] == proposal
    assert stored["decisions"][0]["edited_proposal"] == edited_proposal


@pytest.mark.parametrize(
    "change",
    [
        lambda proposal, context: proposal.pop("start"),
        lambda proposal, context: proposal.update(start="2026-10-16T18:00:00"),
        lambda proposal, context: context.update(blocked_time="none"),
        lambda proposal, context: context.update(
            blocked_time=[
                {
                    "start": "2026-10-16T12:00:00+05:30",
                    "end": "2026-10-16T11:00:00+05:30",
                }
            ]
        ),
        lambda proposal, context: context.update(constraints="must be a list"),
    ],
)
def test_invalid_creation_payload_leaves_no_partial_case(tmp_path, change) -> None:
    service = make_service(tmp_path)
    title, proposal, context = make_payload()
    before = service.list_cases()
    change(proposal, context)

    with pytest.raises(ValueError):
        service.create_case(title, proposal, context)

    assert service.list_cases() == before
    assert all(
        event["event_type"] != "case_created"
        for case_summary in before
        for event in service.get_case(case_summary["id"])["audit"]
    )


def test_seeded_cases_remain_present_after_creation(tmp_path) -> None:
    service = make_service(tmp_path)
    seeded_ids = {case["id"] for case in service.list_cases()}
    title, proposal, context = make_payload()

    service.create_case(title, proposal, context)

    assert seeded_ids <= {case["id"] for case in service.list_cases()}
