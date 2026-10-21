from datetime import date

from mule_copilot import clock

HOLD = date(2026, 11, 2)


def test_explanation_due_on_day_20_and_decision_on_day_30_without_one():
    d = clock.deadlines(HOLD)
    assert d["explanation_due"] == date(2026, 11, 22)
    assert d["decision_due"] == date(2026, 12, 2)
    assert d["max_hold_end"] == date(2027, 1, 1)


def test_decision_due_10_days_after_a_timely_explanation():
    assert clock.deadlines(HOLD, explanation_date="2026-11-08")["decision_due"] == date(2026, 11, 18)


def test_late_explanation_does_not_extend_the_day_30_decision():
    assert clock.deadlines(HOLD, explanation_date="2026-11-25")["decision_due"] == date(2026, 12, 2)


def test_release_after_30_day_lea_window_never_beyond_60_days():
    d = clock.deadlines(HOLD, reference_date="2026-12-02")   # referred on day 30
    assert d["release_on"] == date(2027, 1, 1) == d["max_hold_end"]
    early = clock.deadlines(HOLD, explanation_date="2026-11-05", reference_date="2026-11-10")
    assert early["release_on"] == date(2026, 12, 10)


def test_physical_notice_allowed_until_next_day():
    assert clock.deadlines(HOLD, digital=False)["notify_by"] == date(2026, 11, 3)


def test_next_deadline_counts_days_left():
    d = clock.deadlines(HOLD)
    assert clock.next_deadline("awaiting_explanation", d, date(2026, 11, 20)) == ("Explanation due", date(2026, 11, 22), 2)
    assert clock.next_deadline("closed", d, HOLD) is None
