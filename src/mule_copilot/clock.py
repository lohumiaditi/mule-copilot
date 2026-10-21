"""SOP clock: deadlines for a temporary debit hold, computed from the draft SOP (Annex III).

"Days" means calendar days (para 3). Day counts follow the SOP's own arithmetic in para 5:
a hold lasts at most 60 days, "up to 30 days for Steps 3-4, plus up to 30 further days
for Steps 5-6". So "the 31st day from the date of reference" is read with the reference
date as day 1, i.e. reference + 30 days, which keeps the total within 60.

    hold placed (step 1)              day 0
    notify customer (step 2)          same day if digital, else by end of next day
    explanation due (step 3)          hold + 20
    bank decision due (step 4)        explanation + 10, or hold + 30 if none by day 20
    LEA instruction window (step 5)   reference + 30
    release if no instruction (6)     reference + 30, never later than hold + 60
"""

from datetime import date, timedelta

EXPLANATION_DAYS = 20
DECISION_AFTER_EXPLANATION_DAYS = 10
DECISION_NO_EXPLANATION_DAYS = 30
LEA_WINDOW_DAYS = 30
MAX_HOLD_DAYS = 60


def _d(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


def deadlines(hold_date, explanation_date=None, reference_date=None, digital=True):
    hold = _d(hold_date)
    out = {
        "notify_by": hold if digital else hold + timedelta(days=1),
        "explanation_due": hold + timedelta(days=EXPLANATION_DAYS),
        "max_hold_end": hold + timedelta(days=MAX_HOLD_DAYS),
    }
    explained = _d(explanation_date) if explanation_date else None
    if explained and explained <= out["explanation_due"]:
        out["decision_due"] = explained + timedelta(days=DECISION_AFTER_EXPLANATION_DAYS)
    else:   # no explanation in time: decide on due diligence by day 30
        out["decision_due"] = hold + timedelta(days=DECISION_NO_EXPLANATION_DAYS)
    if reference_date:
        ref = _d(reference_date)
        out["lea_window_end"] = ref + timedelta(days=LEA_WINDOW_DAYS)
        out["release_on"] = min(ref + timedelta(days=LEA_WINDOW_DAYS), out["max_hold_end"])
    return out


# Which deadline the case is waiting on, by workflow stage.
NEXT = {
    "awaiting_explanation": ("Explanation due", "explanation_due"),
    "awaiting_decision": ("Bank decision due", "decision_due"),
    "awaiting_lea": ("Release if no LEA instruction", "release_on"),
}


def next_deadline(stage, dl, today):
    """(label, due date, days left) for the stage, or None when nothing is pending."""
    if stage not in NEXT or not dl:
        return None
    label, key = NEXT[stage]
    due = _d(dl[key])
    return label, due, (due - _d(today)).days
