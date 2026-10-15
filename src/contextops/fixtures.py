"""Fictional, deterministic scheduling cases used by the MVP."""

from __future__ import annotations

from typing import Any


def fictional_cases() -> list[dict[str, Any]]:
    """Return cases without consulting a calendar or an external AI service."""

    shared_context = {
        "timezone": "Asia/Kolkata",
        "working_hours": {"start": "09:00", "end": "17:00"},
        "constraints": [
            "Keep the review within 09:00–17:00 Asia/Kolkata.",
            "Do not assert attendee availability without calendar evidence.",
        ],
        "calendar_data": {
            "status": "not_provided",
            "note": "This fictional offline case has no live attendee calendar feed.",
        },
        "blocked_time": [],
    }
    return [
        {
            "id": "case-outside-hours",
            "title": "Quarterly design review proposed after hours",
            "original_proposal": {
                "event": "Quarterly design review",
                "start": "2026-10-16T18:00:00+05:30",
                "duration_minutes": 60,
                "timezone": "Asia/Kolkata",
            },
            "context": {
                **shared_context,
                "availability": [
                    {
                        "start": "2026-10-16T09:00:00+05:30",
                        "end": "2026-10-16T17:00:00+05:30",
                        "status": "available",
                        "source": "fictional fixture",
                    }
                ],
            },
        },
        {
            "id": "case-similar-outside-hours",
            "title": "Roadmap review proposed after hours",
            "original_proposal": {
                "event": "Roadmap review",
                "start": "2026-10-19T18:30:00+05:30",
                "duration_minutes": 45,
                "timezone": "Asia/Kolkata",
            },
            "context": {
                **shared_context,
                "availability": [
                    {
                        "start": "2026-10-19T09:00:00+05:30",
                        "end": "2026-10-19T17:00:00+05:30",
                        "status": "available",
                        "source": "fictional fixture",
                    }
                ],
            },
        },
    ]
