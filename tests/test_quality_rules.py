from contextops.quality_rules import (
    EvaluationResult,
    QualityRule,
    RuleStatus,
    evaluate_rule,
)


def make_rule() -> QualityRule:
    return QualityRule(
        title="Forbidden response content",
        description="Responses must not contain the forbidden phrase.",
        failure_pattern="forbidden phrase",
        triggering_condition="response",
        expected_behavior="Return a response without the forbidden phrase.",
        test_case="response contains a forbidden phrase",
    )


def test_new_rules_are_drafts_and_are_not_evaluated() -> None:
    rule = make_rule()

    assert rule.status is RuleStatus.DRAFT
    assert evaluate_rule(rule) is None
    assert rule.evaluation_result is None


def test_activation_allows_a_matching_evaluation() -> None:
    rule = make_rule()

    rule.activate()
    result = evaluate_rule(rule)

    assert rule.status is RuleStatus.ACTIVE
    assert result is EvaluationResult.MATCHED
    assert result.matched
    assert rule.evaluation_result is EvaluationResult.MATCHED


def test_disabled_rules_are_not_evaluated() -> None:
    rule = make_rule().activate().disable()

    assert rule.status is RuleStatus.DISABLED
    assert evaluate_rule(rule) is None
    assert rule.evaluation_result is None
