"""Deterministic contextual checks for fictional scheduling proposals."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def detect_issues(proposal: dict[str, Any], context: dict[str, Any]) -> list[dict[str, str]]:
    """Return only evidence supported by the supplied proposal and context."""

    issues: list[dict[str, str]] = []
    working_hours = context.get("working_hours")
    if working_hours and proposal.get("start"):
        start = _parse_datetime(proposal["start"])
        opening = _parse_time(working_hours["start"])
        closing = _parse_time(working_hours["end"])
        if not opening <= start.timetz().replace(tzinfo=None) < closing:
            issues.append(
                {
                    "code": "outside_working_hours",
                    "category": "confirmed_violation",
                    "title": "Proposal is outside stated working hours",
                    "evidence": (
                        f"The proposal starts at {start.strftime('%H:%M')} {proposal.get('timezone', '')}, "
                        f"outside the stated {working_hours['start']}–{working_hours['end']} "
                        f"window in {context.get('timezone', 'the supplied timezone')}."
                    ),
                    "severity": "high",
                }
            )

    if proposal.get("timezone") and context.get("timezone"):
        if proposal["timezone"] != context["timezone"]:
            issues.append(
                {
                    "code": "timezone_mismatch",
                    "category": "confirmed_violation",
                    "title": "Proposal timezone conflicts with the stated context",
                    "evidence": (
                        f"The proposal uses {proposal['timezone']}, while the context requires "
                        f"{context['timezone']}."
                    ),
                    "severity": "high",
                }
            )

    for blocked in context.get("blocked_time", []):
        proposal_start = _parse_datetime(proposal["start"])
        blocked_start = _parse_datetime(blocked["start"])
        blocked_end = _parse_datetime(blocked["end"])
        if blocked_start <= proposal_start < blocked_end:
            issues.append(
                {
                    "code": "blocked_time_conflict",
                    "category": "confirmed_violation",
                    "title": "Proposal starts during blocked time",
                    "evidence": f"The proposal starts during the supplied blocked interval {blocked['start']}–{blocked['end']}.",
                    "severity": "high",
                }
            )

    calendar_data = context.get("calendar_data", {})
    if calendar_data.get("status") == "not_provided":
        issues.extend(
            [
                {
                    "code": "offline_calendar_warning",
                    "category": "warning",
                    "title": "No live attendee calendar is connected",
                    "evidence": "This offline fixture contains no live calendar feed, so no attendee conflict is asserted.",
                    "severity": "medium",
                },
                {
                    "code": "attendee_availability_unresolved",
                    "category": "unresolved_ambiguity",
                    "title": "Attendee availability is unresolved",
                    "evidence": "The supplied context does not confirm whether attendees are available for this proposal.",
                    "severity": "info",
                },
            ]
        )

    return issues


def case_candidate(case: dict[str, Any], issues: list[dict[str, str]]) -> str:
    """Build a stable text representation for active rule evaluation."""

    issue_codes = " ".join(issue["code"] for issue in issues)
    return f"scheduling action {case['original_proposal']['event']} {issue_codes}".lower()
