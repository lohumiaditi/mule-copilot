"""Checks a drafted case note against its evidence before a human sees it.

1. Every number in the note must appear in the evidence (graph facts, typology result,
   and the text of the retrieved clauses, e.g. "20 days"). Rounding to the note's own
   precision is accepted. A ratio may be written as a percentage only with a % sign
   (0.8149 -> 81.5%).
2. Dates and times are matched whole: 2022-09-10, 10 Sep 2022 or 14:21 must be a date or
   time in the evidence. Their parts never count as numbers, so a wrong count cannot pass
   because it happens to equal an hour or a day of the month.
3. Every [clause ID] cited must exist in the corpus, and at least one must be cited.
"""

import re

CITATION = re.compile(r"\[([^\[\]]+)\]")
ACCOUNT_ID = re.compile(r"\b\d+-[0-9A-F]{6,}\b")
NUMBER = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(\s*(?:%|percent\b))?")
DIGITS = re.compile(r"\d+")   # numbers inside field names, e.g. fan_in_48h, cycles_3_5
# Day shorthand after a date, "2022-09-08/09" or "2022-09-08, 09, 10.": a trailing day counts
# only when punctuation, "and", "to" or the line end follows, so "2022-09-08, 12 transfers" stays a count.
DATE_LIST = re.compile(r"\b(\d{4}-\d{2})-(\d{2})((?:\s*(?:/|,|and|to)\s*\d{2}(?=\s*(?:[,/.;)]|and\b|to\b)|\s*$))+)", re.M)
ISO_MOMENT = re.compile(r"\b(\d{4}-\d{2}-\d{2})(?:[ T](\d{1,2}:\d{2})(?::\d{2})?(?:\.\d+)?Z?)?")
CLOCK = re.compile(r"(?<![\d:])(\d{1,2}:\d{2})(?::\d{2})?(?:\s*([AaPp])\.?[Mm]\b\.?)?(?![\d:])")
SLASH_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")   # DD/MM/YYYY, the usual Indian style
NATURAL_DATE = re.compile(r"\b(?:(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})|([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?),?\s+(\d{4})\b")
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


# LLM output quirks that are formatting, not content: non-breaking hyphens, zero-width
# spaces, and full-width brackets (gpt-oss sometimes cites as 【...】).
NORMALISE = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "【": "[", "】": "]",
                           "​": None, "‌": None, "‍": None, "⁠": None, "﻿": None})


def normalise_citation(cid):
    """Forgive formatting, not content: markdown emphasis, Unicode hyphens and spaces."""
    return " ".join(cid.translate(NORMALISE).strip("*_` ").split())


def _clock(text, meridiem=None):
    """24-hour "HH:MM"; "2:21" with "p" (pm) becomes "14:21"."""
    hours, minutes = text.split(":")
    hours = int(hours) % 12 + (12 if meridiem in ("p", "P") else 0) if meridiem else int(hours)
    return f"{hours:02d}:{minutes}"


def moments(text, known_dates=frozenset()):
    """(dates, times, text with them blanked). A date written another way ("10 Sep 2022",
    "10/09/2022") is taken only when it is one of known_dates, so a clause's "April 1, 2027"
    stays numbers."""
    dates, times = set(), set()

    def known(date, m):
        if date not in known_dates:
            return m.group(0)
        dates.add(date)
        return " "

    def iso(m):
        dates.add(m.group(1))
        if m.group(2):
            times.add(_clock(m.group(2)))
        return " "

    def written(m):
        day, month, month2, day2, year = m.groups()
        number = MONTHS.get((month or month2)[:3].lower())
        return known(number and f"{year}-{number:02d}-{int(day or day2):02d}", m)

    def slashed(m):
        day, month, year = m.groups()
        return known(f"{year}-{int(month):02d}-{int(day):02d}", m)

    def clock(m):
        times.add(_clock(m.group(1), m.group(2)))
        return " "

    def date_list(m):
        dates.update(f"{m.group(1)}-{day}" for day in [m.group(2), *re.findall(r"\d{2}", m.group(3))])
        return " "

    text = DATE_LIST.sub(date_list, text)
    text = ISO_MOMENT.sub(iso, text)
    text = NATURAL_DATE.sub(written, text)
    text = SLASH_DATE.sub(slashed, text)
    return dates, times, CLOCK.sub(clock, text)


def numbers_in(text):
    """(value, decimals, is_percentage) for each number in free text, ignoring account IDs,
    dates and times."""
    out = []
    for token, pct in NUMBER.findall(moments(ACCOUNT_ID.sub(" ", text.translate(NORMALISE)))[2]):
        token = token.rstrip(",").replace(",", "")
        decimals = len(token.split(".")[1]) if "." in token else 0
        out.append((float(token), decimals, bool(pct)))
    return out


def evidence_facts(evidence):
    """All numbers, dates and times in the evidence, as values or inside strings."""
    values, dates, times = {0.0, 1.0}, set(), set()

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            values.add(float(x))
        elif isinstance(x, str):
            d, t, rest = moments(ACCOUNT_ID.sub(" ", x.translate(NORMALISE)))
            dates.update(d)
            times.update(t)
            values.update(v for v, _, _ in numbers_in(rest))
        elif isinstance(x, dict):
            for k, v in x.items():
                values.update(float(d) for d in DIGITS.findall(str(k)))
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(evidence)
    values.update(float(d[:4]) for d in dates)   # a year alone, "in 2022" or "September 2022"
    return values, dates, times


def _supported(value, decimals, pct, allowed):
    tolerance = 0.5 * 10 ** -decimals + 1e-9
    scales = (1, 100) if pct else (1,)
    return any(abs(a * k - value) <= tolerance for a in allowed for k in scales)


def check(note, evidence, known_clause_ids):
    note = note.translate(NORMALISE)
    errors = []
    # Clause IDs always contain spaces; a bracketed single token like [pass_through_min] is not a citation.
    groups = [g for g in CITATION.findall(note) if " " in g.strip()]
    cited = [normalise_citation(cid) for group in groups for cid in group.split(";")]
    if not cited:
        errors.append("No clause is cited.")
    for cid in dict.fromkeys(cited):
        if cid not in known_clause_ids:
            errors.append(f"Cited clause [{cid}] does not exist in the corpus.")

    allowed, known_dates, known_times = evidence_facts(evidence)
    body = CITATION.sub(lambda m: " " if " " in m.group(1).strip() else m.group(0), note)
    dates, times, body = moments(ACCOUNT_ID.sub(" ", body), known_dates)
    errors += [f"Date {d} is not in the evidence." for d in sorted(dates - known_dates)]
    errors += [f"Time {t} is not in the evidence." for t in sorted(times - known_times)]
    for value, decimals, pct in dict.fromkeys(numbers_in(body)):
        if not _supported(value, decimals, pct, allowed):
            shown = f"{value:.{decimals}f}" + ("%" if pct else "")
            errors.append(f"Number {shown} is not in the evidence.")
    return errors


ACCOUNT_LEVEL = re.compile(r"account[- ]level hold|hold on the (entire|whole) account", re.I)


def check_hold_level(text, hold_level):
    """Numbers can be right while the hold is wrong: catch an account-level hold described
    when the rules recommend holding only the flagged amounts."""
    if hold_level == "transaction" and ACCOUNT_LEVEL.search(text.translate(NORMALISE)):
        return ["Text describes an account-level hold, but the typology recommends a transaction-level hold."]
    return []
