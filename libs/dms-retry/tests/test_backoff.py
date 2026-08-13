import random

import pytest
from dms_retry import compute_backoff_seconds


def test_attempt_zero_is_bounded_by_base():
    rng = random.Random(1)
    delay = compute_backoff_seconds(0, base=2.0, cap=300.0, rng=rng)
    assert 0.0 <= delay <= 2.0


def test_exponential_growth_before_cap():
    rng = random.Random(1)
    # base * 2**attempt für attempt=3, base=1.0 -> 8.0, weit unter dem Cap.
    delay = compute_backoff_seconds(3, base=1.0, cap=300.0, rng=rng)
    assert 0.0 <= delay <= 8.0


def test_delay_never_exceeds_cap_even_for_large_attempt():
    rng = random.Random(1)
    delay = compute_backoff_seconds(50, base=1.0, cap=60.0, rng=rng)
    assert 0.0 <= delay <= 60.0


def test_is_deterministic_with_seeded_rng():
    first = compute_backoff_seconds(2, base=1.0, cap=300.0, rng=random.Random(42))
    second = compute_backoff_seconds(2, base=1.0, cap=300.0, rng=random.Random(42))
    assert first == second


def test_negative_attempt_raises():
    with pytest.raises(ValueError):
        compute_backoff_seconds(-1)


def test_default_base_and_cap_produce_sane_range():
    rng = random.Random(7)
    delay = compute_backoff_seconds(0, rng=rng)
    assert 0.0 <= delay <= 1.0
