"""Application services for review, decisions, audit, and quality rules."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from contextops.case_validation import validate_case_creation
from contextops.database import Database, read_json
from contextops.detector import case_candidate, detect_issues
from contextops.fixtures import fictional_cases
from contextops.quality_rules import QualityRule, RuleStatus, evaluate_rule


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContextOpsService:
    """Use-case boundary for the offline ContextOps MVP."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.database.initialize()
        self._seed_if_empty()

    def _seed_if_empty(self) -> None:
        with self.database.session() as connection:
            if connection.execute("SELECT 1 FROM scheduling_cases LIMIT 1").fetchone():
                return
            for case in fictional_cases():
                connection.execute(
                    """
                    INSERT INTO scheduling_cases (id, title, original_proposal_json, context_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        case["id"],
                        case["title"],
                        Database.serialize(case["original_proposal"]),
                        Database.serialize(case["context"]),
                    ),
                )
                for issue in detect_issues(case["original_proposal"], case["context"]):
                    connection.execute(
                        """
                        INSERT INTO detected_issues
                            (id, case_id, code, category, title, evidence, severity)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"{case['id']}:{issue['code']}",
                            case["id"],
                            issue["code"],
                            issue["category"],
                            issue["title"],
                            issue["evidence"],
                            issue["severity"],
                        ),
                    )

    def list_cases(self) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT scheduling_cases.id, scheduling_cases.title
                FROM scheduling_cases
                LEFT JOIN case_archives ON case_archives.case_id = scheduling_cases.id
                WHERE case_archives.case_id IS NULL
                ORDER BY scheduling_cases.id
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self.database.session() as connection:
            case_row = connection.execute(
                """
                SELECT scheduling_cases.*, case_archives.archived_at
                FROM scheduling_cases
                LEFT JOIN case_archives ON case_archives.case_id = scheduling_cases.id
                WHERE scheduling_cases.id = ?
                """,
                (case_id,),
            ).fetchone()
            if case_row is None:
                return None
            issues = connection.execute(
                "SELECT * FROM detected_issues WHERE case_id = ? ORDER BY id", (case_id,)
            ).fetchall()
            decisions = connection.execute(
                "SELECT * FROM human_decisions WHERE case_id = ? ORDER BY id", (case_id,)
            ).fetchall()
            audit = connection.execute(
                "SELECT * FROM audit_events WHERE case_id = ? ORDER BY id", (case_id,)
            ).fetchall()
            evaluations = connection.execute(
                """
                SELECT current_evaluations.*, quality_rules.title AS rule_title,
                       quality_rules.status AS rule_status,
                       detected_issues.case_id AS source_case_id,
                       scheduling_cases.title AS source_case_title
                FROM rule_evaluations AS current_evaluations
                JOIN quality_rules ON quality_rules.id = current_evaluations.rule_id
                LEFT JOIN detected_issues
                    ON detected_issues.id = quality_rules.source_issue_id
                LEFT JOIN scheduling_cases
                    ON scheduling_cases.id = detected_issues.case_id
                WHERE current_evaluations.case_id = ?
                  AND quality_rules.status = 'active'
                  AND current_evaluations.id = (
                      SELECT MAX(latest_evaluation.id)
                      FROM rule_evaluations AS latest_evaluation
                      WHERE latest_evaluation.rule_id = current_evaluations.rule_id
                        AND latest_evaluation.case_id = current_evaluations.case_id
                  )
                ORDER BY current_evaluations.id
                """,
                (case_id,),
            ).fetchall()
            return {
                "id": case_row["id"],
                "title": case_row["title"],
                "archived_at": case_row["archived_at"],
                "original_proposal": read_json(case_row["original_proposal_json"]),
                "context": read_json(case_row["context_json"]),
                "issues": [dict(row) for row in issues],
                "decisions": [
                    {
                        **dict(row),
                        "edited_proposal": (
                            json.loads(row["edited_proposal_json"])
                            if row["edited_proposal_json"]
                            else None
                        ),
                    }
                    for row in decisions
                ],
                "audit": [
                    {**dict(row), "payload": read_json(row["payload_json"])}
                    for row in audit
                ],
                "evaluations": [dict(row) for row in evaluations],
            }

    def archive_case(self, case_id: str) -> dict[str, Any]:
        """Archive a case while preserving its history and rule references."""

        archived_at = _now()
        with self.database.session() as connection:
            case = connection.execute(
                "SELECT id FROM scheduling_cases WHERE id = ?", (case_id,)
            ).fetchone()
            if case is None:
                raise KeyError(case_id)

            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO case_archives (case_id, archived_at)
                VALUES (?, ?)
                """,
                (case_id, archived_at),
            ).rowcount
            if inserted:
                connection.execute(
                    """
                    INSERT INTO audit_events (case_id, event_type, payload_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        case_id,
                        "case_archived",
                        Database.serialize(
                            {
                                "case_id": case_id,
                                "archived_at": archived_at,
                                "strategy": "soft_delete",
                            }
                        ),
                        archived_at,
                    ),
                )
            row = connection.execute(
                "SELECT archived_at FROM case_archives WHERE case_id = ?", (case_id,)
            ).fetchone()
            assert row is not None
            return {
                "case_id": case_id,
                "archived": True,
                "archived_at": row["archived_at"],
            }

    def create_case(
        self,
        title: str,
        original_proposal: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate and persist one manually supplied scheduling case atomically."""

        validate_case_creation(title, original_proposal, context)
        issues = detect_issues(original_proposal, context)
        with self.database.session() as connection:
            for _ in range(10):
                case_id = f"case-{uuid.uuid4().hex}"
                if connection.execute(
                    "SELECT 1 FROM scheduling_cases WHERE id = ?", (case_id,)
                ).fetchone() is None:
                    break
            else:
                raise RuntimeError("could not generate a unique case id")

            connection.execute(
                """
                INSERT INTO scheduling_cases (id, title, original_proposal_json, context_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    case_id,
                    title,
                    Database.serialize(original_proposal),
                    Database.serialize(context),
                ),
            )
            for index, issue in enumerate(issues):
                issue_id = f"{case_id}:{issue['code']}"
                if connection.execute(
                    "SELECT 1 FROM detected_issues WHERE id = ?", (issue_id,)
                ).fetchone() is not None:
                    issue_id = f"{case_id}:{issue['code']}:{index}"
                connection.execute(
                    """
                    INSERT INTO detected_issues
                        (id, case_id, code, category, title, evidence, severity)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        issue_id,
                        case_id,
                        issue["code"],
                        issue["category"],
                        issue["title"],
                        issue["evidence"],
                        issue["severity"],
                    ),
                )
            connection.execute(
                """
                INSERT INTO audit_events (case_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    case_id,
                    "case_created",
                    Database.serialize(
                        {"case_id": case_id, "title": title, "issue_count": len(issues)}
                    ),
                    _now(),
                ),
            )
        created_case = self.get_case(case_id)
        assert created_case is not None
        return created_case

    def record_decision(
        self,
        case_id: str,
        decision: str,
        rationale: str,
        edited_proposal: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if decision not in {"accept", "approve", "edit", "reject", "defer", "escalate"}:
            raise ValueError(
                "decision must be approve (or accept), edit, reject, defer, or escalate"
            )
        if not rationale.strip():
            raise ValueError("rationale is required")
        if decision == "edit" and edited_proposal is None:
            raise ValueError("edited_proposal is required when decision is edit")
        if edited_proposal is not None and not isinstance(edited_proposal, dict):
            raise ValueError("edited_proposal must be an object")
        if self.get_case(case_id) is None:
            raise KeyError(case_id)
        created_at = _now()
        with self.database.session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO human_decisions
                    (case_id, decision, rationale, edited_proposal_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    decision,
                    rationale.strip(),
                    Database.serialize(edited_proposal) if edited_proposal else None,
                    created_at,
                ),
            )
            payload = {
                "decision": decision,
                "rationale": rationale.strip(),
                "edited_proposal": edited_proposal,
                "preserved_original_proposal": True,
            }
            connection.execute(
                """
                INSERT INTO audit_events (case_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (case_id, "human_decision_recorded", Database.serialize(payload), created_at),
            )
            decision_id = cursor.lastrowid
        return {"id": decision_id, **payload, "created_at": created_at}

    def create_rule_from_issue(self, case_id: str, issue_id: str) -> dict[str, Any]:
        with self.database.session() as connection:
            issue = connection.execute(
                "SELECT * FROM detected_issues WHERE id = ? AND case_id = ?",
                (issue_id, case_id),
            ).fetchone()
            if issue is None:
                raise KeyError(issue_id)
            rule = QualityRule(
                title=f"Prevent: {issue['title']}",
                description=(
                    f"When a scheduling action shows the pattern '{issue['code']}', "
                    "review it before execution."
                ),
                failure_pattern=issue["code"],
                triggering_condition="scheduling action",
                expected_behavior=(
                    "Reject or revise the proposal and show the supporting evidence "
                    "before execution."
                ),
                test_case=f"scheduling action {issue['code']}",
            )
            existing = connection.execute(
                """
                SELECT *
                FROM quality_rules
                WHERE failure_pattern = ?
                  AND triggering_condition = ?
                  AND expected_behavior = ?
                ORDER BY CASE status
                    WHEN 'active' THEN 0
                    WHEN 'draft' THEN 1
                    WHEN 'disabled' THEN 2
                    ELSE 3
                END, id
                LIMIT 1
                """,
                (
                    rule.failure_pattern,
                    rule.triggering_condition,
                    rule.expected_behavior,
                ),
            ).fetchone()
            if existing is not None:
                existing_rule_id = int(existing["id"])
            else:
                existing_rule_id = None

            if existing_rule_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO quality_rules
                        (source_issue_id, title, description, failure_pattern,
                         triggering_condition, expected_behavior, test_case,
                         evaluation_result, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        issue_id,
                        rule.title,
                        rule.description,
                        rule.failure_pattern,
                        rule.triggering_condition,
                        rule.expected_behavior,
                        rule.test_case,
                        None,
                        rule.status.value,
                    ),
                )
                rule_id = cursor.lastrowid
                connection.execute(
                    """
                    INSERT INTO audit_events (case_id, event_type, payload_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        case_id,
                        "quality_rule_drafted",
                        Database.serialize({"rule_id": rule_id, "status": rule.status.value}),
                        _now(),
                    ),
                )
            else:
                rule_id = existing_rule_id
        result = self.get_rule(int(rule_id))
        assert result is not None
        if existing_rule_id is not None:
            result.update(
                {
                    "duplicate": True,
                    "created": False,
                    "duplicate_status": result["status"],
                    "duplicate_message": (
                        "An equivalent quality rule already exists; "
                        "returning the existing rule without creating a duplicate."
                    ),
                }
            )
        else:
            result.update({"created": True, "duplicate": False})
        return result

    def list_rules(self) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT quality_rules.*,
                       detected_issues.case_id AS source_case_id,
                       scheduling_cases.title AS source_case_title
                FROM quality_rules
                LEFT JOIN detected_issues
                    ON detected_issues.id = quality_rules.source_issue_id
                LEFT JOIN scheduling_cases
                    ON scheduling_cases.id = detected_issues.case_id
                ORDER BY quality_rules.id
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_rule(self, rule_id: int) -> dict[str, Any] | None:
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT quality_rules.*,
                       detected_issues.case_id AS source_case_id,
                       scheduling_cases.title AS source_case_title
                FROM quality_rules
                LEFT JOIN detected_issues
                    ON detected_issues.id = quality_rules.source_issue_id
                LEFT JOIN scheduling_cases
                    ON scheduling_cases.id = detected_issues.case_id
                WHERE quality_rules.id = ?
                """,
                (rule_id,),
            ).fetchone()
            return dict(row) if row else None

    def activate_rule(self, rule_id: int) -> dict[str, Any]:
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT quality_rules.*, detected_issues.case_id AS source_case_id
                FROM quality_rules
                LEFT JOIN detected_issues ON detected_issues.id = quality_rules.source_issue_id
                WHERE quality_rules.id = ?
                """,
                (rule_id,),
            ).fetchone()
            if row is None:
                raise KeyError(rule_id)
            connection.execute(
                "UPDATE quality_rules SET status = 'active' WHERE id = ?", (rule_id,)
            )
            connection.execute(
                """
                INSERT INTO audit_events (case_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    row["source_case_id"],
                    "quality_rule_activated",
                    Database.serialize({"rule_id": rule_id, "status": RuleStatus.ACTIVE.value}),
                    _now(),
                ),
            )
        return self.get_rule(rule_id)

    def disable_rule(self, rule_id: int) -> dict[str, Any]:
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT quality_rules.id, detected_issues.case_id AS source_case_id
                FROM quality_rules
                LEFT JOIN detected_issues ON detected_issues.id = quality_rules.source_issue_id
                WHERE quality_rules.id = ?
                """,
                (rule_id,),
            ).fetchone()
            if row is None:
                raise KeyError(rule_id)
            connection.execute(
                "UPDATE quality_rules SET status = 'disabled', evaluation_result = NULL WHERE id = ?",
                (rule_id,),
            )
            connection.execute(
                """
                INSERT INTO audit_events (case_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    row["source_case_id"],
                    "quality_rule_disabled",
                    Database.serialize({"rule_id": rule_id, "status": RuleStatus.DISABLED.value}),
                    _now(),
                ),
            )
        return self.get_rule(rule_id)

    def evaluate_active_rules(self, case_id: str) -> list[dict[str, Any]]:
        case = self.get_case(case_id)
        if case is None:
            raise KeyError(case_id)
        issues = case["issues"]
        candidate = case_candidate(case, issues)
        evaluated: list[dict[str, Any]] = []
        for stored_rule in self.list_rules():
            if stored_rule["status"] != RuleStatus.ACTIVE.value:
                continue
            rule = QualityRule(
                title=stored_rule["title"],
                description=stored_rule["description"],
                failure_pattern=stored_rule["failure_pattern"],
                triggering_condition=stored_rule["triggering_condition"],
                expected_behavior=stored_rule["expected_behavior"],
                test_case=stored_rule["test_case"],
            ).activate()
            result = evaluate_rule(rule, candidate)
            assert result is not None
            with self.database.session() as connection:
                connection.execute(
                    "UPDATE quality_rules SET evaluation_result = ? WHERE id = ?",
                    (result.value, stored_rule["id"]),
                )
                connection.execute(
                    """
                    INSERT INTO rule_evaluations (rule_id, case_id, result, evaluated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (stored_rule["id"], case_id, result.value, _now()),
                )
                connection.execute(
                    """
                    INSERT INTO audit_events (case_id, event_type, payload_json, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        case_id,
                        "quality_rule_evaluated",
                        Database.serialize(
                            {
                                "rule_id": stored_rule["id"],
                                "result": result.value,
                            }
                        ),
                        _now(),
                    ),
                )
            evaluated.append(
                {
                    "rule_id": stored_rule["id"],
                    "case_id": case_id,
                    "rule_title": stored_rule["title"],
                    "result": result.value,
                }
            )
        return evaluated
