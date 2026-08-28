from __future__ import annotations

from groundtruth.retrieval.budget import Budget, BudgetLimits


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_defaults_match_spec_8_2() -> None:
    limits = BudgetLimits()
    assert limits.max_tool_calls == 30
    assert limits.max_wall_clock_s == 60
    assert limits.grep_max_matches == 50
    assert limits.grep_max_bytes == 64 * 1024
    assert limits.read_max_bytes == 32 * 1024


class TestExhaustion:
    def test_tool_call_ceiling(self) -> None:
        budget = Budget(BudgetLimits(max_tool_calls=3))
        calls = 0
        while not budget.exhausted:
            budget.record_tool_call()
            calls += 1
        assert calls == 3
        assert budget.tripped_limit == "max_tool_calls"

    def test_wall_clock_ceiling_with_injected_clock(self) -> None:
        clock = _FakeClock()
        budget = Budget(BudgetLimits(max_wall_clock_s=60), clock=clock)
        assert budget.exhausted is False
        clock.advance(59)
        assert budget.exhausted is False
        clock.advance(1)
        assert budget.exhausted is True
        assert budget.tripped_limit == "max_wall_clock_s"

    def test_exhaustion_never_raises(self) -> None:
        budget = Budget(BudgetLimits(max_tool_calls=1))
        for _ in range(10):
            budget.record_tool_call()
        assert budget.exhausted is True  # no exception

    def test_reports_not_interprets(self) -> None:
        # The budget exposes exhaustion + which limit; it never decides
        # "fail the job" vs "refuse".
        budget = Budget(BudgetLimits(max_tool_calls=1))
        budget.record_tool_call()
        assert budget.exhausted is True
        assert budget.tripped_limit == "max_tool_calls"
        assert not hasattr(budget, "refuse")
        assert not hasattr(budget, "fail_job")


class TestByteCaps:
    def test_grep_max_matches_flagged(self) -> None:
        budget = Budget(BudgetLimits(grep_max_matches=2))
        clamped = budget.clamp_matches(["a", "b", "c", "d"])
        assert clamped.value == ["a", "b"]
        assert clamped.truncated is True

    def test_grep_max_matches_not_flagged_when_under(self) -> None:
        budget = Budget(BudgetLimits(grep_max_matches=10))
        clamped = budget.clamp_matches(["a", "b"])
        assert clamped.value == ["a", "b"]
        assert clamped.truncated is False

    def test_grep_max_bytes_flagged(self) -> None:
        budget = Budget(BudgetLimits(grep_max_bytes=5))
        clamped = budget.clamp_grep_output("abcdefghij")
        assert clamped.value == "abcde"
        assert clamped.truncated is True

    def test_read_max_bytes_flagged(self) -> None:
        budget = Budget(BudgetLimits(read_max_bytes=4))
        clamped = budget.clamp_read("hello world")
        assert clamped.value == "hell"
        assert clamped.truncated is True

    def test_read_under_cap_is_not_truncated(self) -> None:
        budget = Budget(BudgetLimits(read_max_bytes=100))
        clamped = budget.clamp_read("hello")
        assert clamped.value == "hello"
        assert clamped.truncated is False

    def test_byte_clamp_does_not_split_a_utf8_char(self) -> None:
        budget = Budget(BudgetLimits(read_max_bytes=2))  # "é" is 2 bytes
        clamped = budget.clamp_read("aébc")  # bytes: 61 c3 a9 62 63
        assert clamped.value == "a"  # the cap lands inside 'é'; the partial byte is dropped
        assert clamped.truncated is True


class TestOverridesAndIsolation:
    def test_per_vault_overrides_take_effect(self) -> None:
        budget = Budget(BudgetLimits(max_tool_calls=5, grep_max_matches=1))
        for _ in range(5):
            budget.record_tool_call()
        assert budget.exhausted is True
        assert budget.clamp_matches(["a", "b"]).value == ["a"]

    def test_budget_is_not_shared_across_runs(self) -> None:
        limits = BudgetLimits(max_tool_calls=2)
        run_a = Budget(limits)
        run_b = Budget(limits)
        run_a.record_tool_call()
        run_a.record_tool_call()
        assert run_a.exhausted is True
        assert run_b.exhausted is False


def test_from_config_limits() -> None:
    class _Limits:
        max_tool_calls = 7
        max_wall_clock_s = 12
        grep_max_matches = 9
        grep_max_bytes = 1000
        read_max_bytes = 500

    limits = BudgetLimits.from_limits(_Limits())
    assert limits.max_tool_calls == 7
    assert limits.read_max_bytes == 500
