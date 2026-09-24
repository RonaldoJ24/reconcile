from __future__ import annotations

from reconcile.interpretation.budget import BudgetPolicy, RateCard


def test_peak_rate_card_rounds_up_worst_case_reservation() -> None:
    rate = RateCard()

    assert rate.verified_on == "2026-09-23"
    assert rate.estimated_microdollars(input_tokens=6_000, output_tokens=2_048) == 1_774
    assert (
        rate.estimated_microdollars(
            input_tokens=6_000, output_tokens=2_048, cached_input_tokens=6_000
        )
        == 1_084
    )


def test_budget_policy_has_bounded_attempt_and_token_totals() -> None:
    policy = BudgetPolicy()

    assert policy.execution_microdollars == 50_000
    assert policy.session_attempts == 5
    assert policy.global_day_attempts == 25
    assert policy.execution_attempts == 25
    assert policy.session_tokens == 40_240
    assert policy.day_tokens == 201_200
