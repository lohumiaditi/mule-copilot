"""Checks a drafted case note against its evidence before a human sees it.

1. Every number in the note must appear in the evidence (graph facts, typology result,
   and the text of the retrieved clauses, e.g. "20 days"). Rounding to the note's own
   precision is accepted, as is a ratio written as a percentage (0.8149 -> 81.5%).
2. Every [clause ID] cited must exist in the corpus, and at least one must be cited.
"""

import json
import re

CITATION = re.compile(r"\[([^\[\]]+)\]")
ACCOUNT_ID = re.compile(r"\b\d+-[0-9A-F]{6,}\b")
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
DIGITS = re.compile(r"\d+")   # numbers inside field names, e.g. fan_in_48h, cycles_3_5


# LLM output quirks that are formatting, not content: non-breaking hyphens, zero-width
# spaces, and full-width brackets (gpt-oss sometimes cites as 【...】).
NORMALISE = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "【": "[", "】": "]",
                           "​": None, "‌": None, "‍": None, "⁠": None, "﻿": None})


def _strip(text):
    return ACCOUNT_ID.sub(" ", text.translate(NORMALISE))


def normalise_citation(cid):
    """Forgive formatting, not content: markdown emphasis, Unicode hyphens and spaces."""
    return " ".join(cid.translate(NORMALISE).strip("*_` ").split())


def numbers_in(text):
    """(value, decimals) for each number in free text, ignoring account IDs."""
    out = []
    for token in NUMBER.findall(_strip(text)):
        token = token.rstrip(",").replace(",", "")
        decimals = len(token.split(".")[1]) if "." in token else 0
        out.append((float(token), decimals))
    return out


def evidence_numbers(evidence):
    """All numbers in the evidence, whether stored as values or inside strings."""
    values = set()

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            values.add(float(x))
        elif isinstance(x, str):
            values.update(v for v, _ in numbers_in(x))
        elif isinstance(x, dict):
            for k, v in x.items():
                values.update(float(d) for d in DIGITS.findall(str(k)))
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(evidence)
    return values


def _supported(value, decimals, allowed):
    tolerance = 0.5 * 10 ** -decimals + 1e-9
    return any(abs(candidate - value) <= tolerance
               for a in allowed for candidate in (a, a * 100, a / 100))


def check(note, evidence, known_clause_ids):
    note = note.translate(NORMALISE)
    errors = []
    cited = [normalise_citation(cid) for group in CITATION.findall(note) for cid in group.split(";")]
    if not cited:
        errors.append("No clause is cited.")
    for cid in dict.fromkeys(cited):
        if cid not in known_clause_ids:
            errors.append(f"Cited clause [{cid}] does not exist in the corpus.")

    allowed = evidence_numbers(evidence) | {0.0, 1.0}
    body = CITATION.sub(" ", note)
    for value, decimals in dict.fromkeys(numbers_in(body)):
        if not _supported(value, decimals, allowed):
            shown = f"{value:.{decimals}f}"
            errors.append(f"Number {shown} is not in the evidence.")
    return errors


ACCOUNT_LEVEL = re.compile(r"account[- ]level hold|hold on the (entire|whole) account", re.I)


def check_hold_level(text, hold_level):
    """Numbers can be right while the hold is wrong: catch an account-level hold described
    when the rules recommend holding only the flagged amounts."""
    if hold_level == "transaction" and ACCOUNT_LEVEL.search(text.translate(NORMALISE)):
        return ["Text describes an account-level hold, but the typology recommends a transaction-level hold."]
    return []


if __name__ == "__main__":   # quick manual check: python -m mule_copilot.verifier note.md evidence.json
    import sys
    from mule_copilot.corpus import clauses_by_id
    note = open(sys.argv[1], encoding="utf-8").read()
    evidence = json.load(open(sys.argv[2], encoding="utf-8"))
    print("\n".join(check(note, evidence, clauses_by_id().keys())) or "OK")
