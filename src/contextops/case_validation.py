"""Validation for offline manual case creation payloads."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any


def _require_non_empty_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def _parse_offset_datetime(value: Any, field: str) -> datetime:
    _require_non_empty_string(value, field)
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include an explicit UTC offset")
    return parsed


def _parse_time(value: Any, field: str) -> time:
    _require_non_empty_string(value, field)
    try:
        parsed = time.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a valid time") from error
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        raise ValueError(f"{field} must be a local wall-clock time without an offset")
    return parsed


def _validate_intervals(intervals: Any, field: str) -> None:
    if not isinstance(intervals, list):
        raise ValueError(f"{field} must be a list")
    for index, interval in enumerate(intervals):
        item_field = f"{field}[{index}]"
        if not isinstance(interval, dict):
            raise ValueError(f"{item_field} must be an object")
        start = _parse_offset_datetime(interval.get("start"), f"{item_field}.start")
        end = _parse_offset_datetime(interval.get("end"), f"{item_field}.end")
        if start >= end:
            raise ValueError(f"{item_field}.start must be before {item_field}.end")


def validate_case_creation(
    title: Any,
    original_proposal: Any,
    context: Any,
) -> None:
    """Validate all required scheduling data before any persistence begins."""

    _require_non_empty_string(title, "title")

    if not isinstance(original_proposal, dict):
        raise ValueError("original_proposal must be an object")
    _require_non_empty_string(original_proposal.get("event"), "original_proposal.event")
    _parse_offset_datetime(original_proposal.get("start"), "original_proposal.start")
    duration_minutes = original_proposal.get("duration_minutes")
    if type(duration_minutes) is not int or duration_minutes <= 0:
        raise ValueError("original_proposal.duration_minutes must be a positive integer")
    _require_non_empty_string(original_proposal.get("timezone"), "original_proposal.timezone")

    if not isinstance(context, dict):
        raise ValueError("context must be an object")
    _require_non_empty_string(context.get("timezone"), "context.timezone")

    working_hours = context.get("working_hours")
    if not isinstance(working_hours, dict):
        raise ValueError("context.working_hours must be an object")
    opening = _parse_time(working_hours.get("start"), "context.working_hours.start")
    closing = _parse_time(working_hours.get("end"), "context.working_hours.end")
    try:
        hours_are_ordered = opening < closing
    except TypeError as error:
        raise ValueError(
            "context.working_hours.start and end must use comparable times"
        ) from error
    if not hours_are_ordered:
        raise ValueError("context.working_hours.start must be before context.working_hours.end")

    calendar_data = context.get("calendar_data")
    if not isinstance(calendar_data, dict):
        raise ValueError("context.calendar_data must be an object")
    _require_non_empty_string(calendar_data.get("status"), "context.calendar_data.status")

    _validate_intervals(context.get("blocked_time"), "context.blocked_time")

    for optional_field in ("constraints", "availability"):
        if optional_field in context and not isinstance(context[optional_field], list):
            raise ValueError(f"context.{optional_field} must be a list")
