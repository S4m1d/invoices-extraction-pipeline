from datetime import date
from decimal import Decimal

import pytest

from fields_extractor.budget import DailyBudget
from fields_extractor.llm import LlmDailyBudget
from fields_extractor.storage import StorageUnavailable


class FakeStore:
    def __init__(self):
        self.objects: dict[tuple[str, str], dict] = {}
        self.fail_reads = False
        self.fail_writes = 0

    def list_usage(self, day):
        if self.fail_reads:
            raise StorageUnavailable("down")
        return [dict(c) for (d, _), c in self.objects.items() if d == day]

    def put_usage(self, day, instance, counter):
        if self.fail_writes:
            self.fail_writes -= 1
            raise StorageUnavailable("down")
        self.objects[(day, instance)] = dict(counter)


def counter(day, instance, spent):
    return {"date": day, "instance": instance, "spent_usd": spent, "input_tokens": 1, "output_tokens": 1, "requests": 1}


class Clock:
    def __init__(self, d):
        self.d = d

    def __call__(self):
        return self.d


@pytest.fixture
def env():
    store, clock = FakeStore(), Clock(date(2026, 10, 15))
    return store, clock, DailyBudget(store, Decimal("10"), "me", today=clock)


def test_sums_all_instances(env):
    store, _, b = env
    store.objects[("2026-10-15", "a")] = counter("2026-10-15", "a", "3.5")
    store.objects[("2026-10-15", "b")] = counter("2026-10-15", "b", "1.25")
    store.objects[("2026-10-14", "a")] = counter("2026-10-14", "a", "100")  # other day
    b.add(Decimal("0.25"), 10, 20)
    assert b.spent_today() == Decimal("5.000000")


def test_add_writes_own_counter(env):
    store, _, b = env
    b.add(Decimal("0.1"), 100, 200)
    b.add(Decimal("0.2"), 1, 2)
    assert store.objects[("2026-10-15", "me")] == {
        "date": "2026-10-15",
        "instance": "me",
        "spent_usd": "0.300000",
        "input_tokens": 101,
        "output_tokens": 202,
        "requests": 2,
    }


def test_restart_continues_stored_counter(env):
    store, clock, _ = env
    store.objects[("2026-10-15", "me")] = counter("2026-10-15", "me", "4")
    b = DailyBudget(store, Decimal("10"), "me", today=clock)
    assert b.spent_today() == Decimal("4")  # not counted twice
    b.add(Decimal("1"), 1, 1)
    assert store.objects[("2026-10-15", "me")]["spent_usd"] == "5.000000"
    assert store.objects[("2026-10-15", "me")]["requests"] == 2


def test_new_day_starts_at_zero(env):
    _, clock, b = env
    b.add(Decimal("9"), 1, 1)
    clock.d = date(2026, 10, 16)
    assert b.spent_today() == 0
    b.add(Decimal("1"), 1, 1)
    assert b.spent_today() == Decimal("1")


def test_ensure_available(env):
    store, _, b = env
    b.ensure_available()
    assert b.available()
    store.objects[("2026-10-15", "x")] = counter("2026-10-15", "x", "10")
    with pytest.raises(LlmDailyBudget, match=r"daily budget reached: spent \$10\.0000 of \$10\.00 today \(UTC\)"):
        b.ensure_available()
    assert not b.available()


def test_failed_write_is_kept_and_written_later(env):
    store, _, b = env
    store.fail_writes = 1
    b.add(Decimal("1"), 1, 1)
    assert ("2026-10-15", "me") not in store.objects
    assert b.spent_today() == Decimal("1")  # still counted locally
    b.add(Decimal("2"), 1, 1)
    assert store.objects[("2026-10-15", "me")]["spent_usd"] == "3.000000"


def test_unreadable_spend_raises(env):
    store, _, b = env
    store.fail_reads = True
    with pytest.raises(StorageUnavailable):
        b.ensure_available()


def test_error_code():
    assert LlmDailyBudget.code == "llm_daily_budget_reached"
