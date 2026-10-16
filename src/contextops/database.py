"""Small SQLite persistence layer with no external service dependency."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS scheduling_cases (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    original_proposal_json TEXT NOT NULL,
    context_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS detected_issues (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES scheduling_cases(id),
    code TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    evidence TEXT NOT NULL,
    severity TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS human_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL REFERENCES scheduling_cases(id),
    decision TEXT NOT NULL,
    rationale TEXT NOT NULL,
    edited_proposal_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quality_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_issue_id TEXT REFERENCES detected_issues(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    failure_pattern TEXT NOT NULL,
    triggering_condition TEXT NOT NULL,
    expected_behavior TEXT NOT NULL,
    test_case TEXT NOT NULL,
    evaluation_result TEXT,
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'disabled'))
);

CREATE TABLE IF NOT EXISTS rule_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL REFERENCES quality_rules(id),
    case_id TEXT NOT NULL REFERENCES scheduling_cases(id),
    result TEXT NOT NULL,
    evaluated_at TEXT NOT NULL
);
"""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def read_json(value: str) -> Any:
    return json.loads(value)


class Database:
    """A request-scoped connection wrapper for the ContextOps schema."""

    def __init__(self, path: str) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        """Open, commit or roll back, and always close one database session."""

        connection = self.connect()
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.session() as connection:
            connection.executescript(SCHEMA)

    @staticmethod
    def serialize(value: Any) -> str:
        return _json(value)
