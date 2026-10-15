"""Domain objects and deterministic evaluation for quality rules.

The module deliberately has no framework or persistence dependencies.  A rule
starts as a draft, can be explicitly activated, and can be disabled again.
Only active rules evaluate their stored test case (or an explicitly supplied
candidate).  Matching uses literal, case-sensitive substring checks so that
the same input always produces the same result.
"""

from dataclasses import dataclass, field
from enum import Enum


class RuleStatus(str, Enum):
    """Lifecycle states supported by a quality rule."""

    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"


class EvaluationResult(str, Enum):
    """Result of evaluating an active rule against a test case."""

    MATCHED = "matched"
    NOT_MATCHED = "not_matched"
    NOT_TRIGGERED = "not_triggered"

    @property
    def matched(self) -> bool:
        """Whether the rule's failure pattern was found."""

        return self is EvaluationResult.MATCHED


@dataclass(slots=True)
class QualityRule:
    """A reviewable quality rule and its most recent evaluation result.

    ``status`` and ``evaluation_result`` are intentionally not constructor
    arguments.  This prevents a newly-created rule from bypassing the draft
    state or claiming an evaluation that has not happened yet.
    """

    title: str
    description: str
    failure_pattern: str
    triggering_condition: str
    expected_behavior: str
    test_case: str
    evaluation_result: EvaluationResult | None = field(default=None, init=False)
    _status: RuleStatus = field(default=RuleStatus.DRAFT, init=False, repr=False)

    def __post_init__(self) -> None:
        """Reject incomplete rule content while keeping conditions flexible."""

        for field_name in (
            "title",
            "description",
            "failure_pattern",
            "expected_behavior",
            "test_case",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")

        if not isinstance(self.triggering_condition, str):
            raise TypeError("triggering_condition must be a string")

    @property
    def status(self) -> RuleStatus:
        """The current lifecycle status."""

        return self._status

    def activate(self) -> "QualityRule":
        """Explicitly make this rule active and eligible for evaluation."""

        self._status = RuleStatus.ACTIVE
        return self

    def disable(self) -> "QualityRule":
        """Disable this rule and clear its stored evaluation result."""

        self._status = RuleStatus.DISABLED
        self.evaluation_result = None
        return self

    def evaluate(self, test_case: str | None = None) -> EvaluationResult | None:
        """Evaluate an active rule deterministically.

        The rule's stored ``test_case`` is used when no candidate is supplied.
        An empty triggering condition means the rule is unconditional.  A
        non-empty condition must occur literally in the candidate before the
        failure pattern is checked.  Draft and disabled rules return ``None``
        and retain no new evaluation result.
        """

        if self.status is not RuleStatus.ACTIVE:
            return None

        candidate = self.test_case if test_case is None else test_case
        if not isinstance(candidate, str):
            raise TypeError("test_case must be a string")

        triggered = not self.triggering_condition or (
            self.triggering_condition in candidate
        )
        if not triggered:
            result = EvaluationResult.NOT_TRIGGERED
        elif self.failure_pattern in candidate:
            result = EvaluationResult.MATCHED
        else:
            result = EvaluationResult.NOT_MATCHED

        self.evaluation_result = result
        return result


def evaluate_rule(
    rule: QualityRule, test_case: str | None = None
) -> EvaluationResult | None:
    """Evaluate ``rule`` through a small integration-friendly function."""

    if not isinstance(rule, QualityRule):
        raise TypeError("rule must be a QualityRule")
    return rule.evaluate(test_case)


__all__ = ["EvaluationResult", "QualityRule", "RuleStatus", "evaluate_rule"]
