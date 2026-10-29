"""Day 4 evals: five numbers on the frozen test sets.

    python -m mule_copilot.evals detection       # rules on the 30 eval accounts, with and without linkage (no LLM)
    python -m mule_copilot.evals notes           # case notes for all 30 accounts (LLM, resumable)
    python -m mule_copilot.evals assessor        # the 15 explanations through the assessor (LLM, resumable)
    python -m mule_copilot.evals sheets          # sheets for the two hand checks: citations, verifier send-backs
    python -m mule_copilot.evals report          # results table -> evals/results.md

Eval runs use their own MIS and checkpoint databases, so demo cases are untouched.
"""

import json
import re
import statistics
import sys
import time

import groq
import pandas as pd

from mule_copilot import agent, assessor, config, corpus, graph_store, typology, verifier

RUNS = config.EVALS / "runs"
NOTES = RUNS / "notes"
DETECTION = RUNS / "detection.csv"
ASSESSOR = RUNS / "assessor.csv"
CITATION_SHEET = config.EVALS / "citation_review.csv"
VERIFIER_SHEET = config.EVALS / "verifier_review.csv"
RESULTS = config.EVALS / "results.md"
MANUAL_MINUTES = 90   # the plan's assumption for a manual case write-up
CASE_DATE = "2026-11-02"


def _use_eval_stores():
    RUNS.mkdir(parents=True, exist_ok=True)
    config.MIS_DB = RUNS / "mis.sqlite"
    config.CHECKPOINTS_DB = RUNS / "checkpoints.sqlite"
    agent.NOTES_DIR = NOTES


def _eval_set():
    return pd.read_csv(config.EVAL_ACCOUNTS)


def _metrics(truth, predicted):
    tp = sum(t and p for t, p in zip(truth, predicted))
    fp = sum(p and not t for t, p in zip(truth, predicted))
    fn = sum(t and not p for t, p in zip(truth, predicted))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


# --- 1. detection ------------------------------------------------------------------------

def detection():
    rows = []
    for r in _eval_set().itertuples():
        a = graph_store.account(r.account_id)
        links = graph_store.reported_links(r.account_id)
        with_l, without = typology.score(a, links), typology.score(a, None)
        rows.append({"account_id": r.account_id, "label": r.label, "selection": r.selection,
                     "signals": "|".join(s["name"] for s in with_l["signals"]),
                     "hold_with_linkage": with_l["hold_level"], "hold_without_linkage": without["hold_level"]})
    df = pd.DataFrame(rows)
    RUNS.mkdir(parents=True, exist_ok=True)
    df.to_csv(DETECTION, index=False)
    print(df.to_string())


# --- 2. case notes (feeds citation accuracy, verifier catch rate, time per case) ----------

RETRY_AFTER = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?")


def _with_rate_limit_waits(fn, label):
    """Run fn; on a Groq rate limit, sleep as long as Groq asks and try again. Other errors: 3 tries."""
    failures = 0
    while True:
        try:
            return fn()
        except groq.RateLimitError as e:
            h, m, s = RETRY_AFTER.search(str(e)).groups() if RETRY_AFTER.search(str(e)) else (0, 1, 0)
            wait = min(int(h or 0) * 3600 + int(m or 0) * 60 + float(s or 0) + 5, 1800)
            print(f"{label}: rate limited, waiting {wait / 60:.1f} min", flush=True)
            time.sleep(wait)
        except Exception as e:
            failures += 1
            print(f"{label}: failed ({failures}): {e!s:.200}", flush=True)
            if failures == 3:
                return None
            time.sleep(30)


def notes():
    _use_eval_stores()
    for r in _eval_set().itertuples():
        if (NOTES / f"{r.account_id}.json").exists():
            continue
        if _with_rate_limit_waits(lambda: agent.open_case(r.account_id, CASE_DATE, use_linkage=True), r.account_id) is None \
                and not (NOTES / f"{r.account_id}.json").exists():
            continue
        record = json.loads((NOTES / f"{r.account_id}.json").read_text(encoding="utf-8"))
        print(f"{r.account_id}: {record['typology']['hold_level']}, drafts {record['drafts']}, "
              f"errors {record['errors']}, {record['seconds']}s, tokens {record.get('tokens')}", flush=True)


# --- 3. explanation assessor -----------------------------------------------------------------

def evidence_for(account_id):
    s = {"account_id": account_id, "use_linkage": True}
    a = graph_store.account(account_id)
    s["facts"] = {k: a[k] for k in agent.FACTS}
    s.update(agent.evidence(s))
    s.update(agent.score(s))
    return agent._evidence(s)


def assess_explanations():
    done = pd.read_csv(ASSESSOR) if ASSESSOR.exists() else pd.DataFrame(columns=["account_id"])
    rows = done.to_dict("records")
    for r in pd.read_csv(config.EXPLANATIONS).itertuples():
        if r.account_id in set(done.account_id):
            continue
        result = _with_rate_limit_waits(lambda: assessor.assess(r.explanation, evidence_for(r.account_id)), r.account_id)
        if result is None:
            continue
        rows.append({"account_id": r.account_id, "difficulty": r.difficulty, "expected": r.expected,
                     "predicted": result["recommendation"], "reasons": result["reasons"],
                     "consistent": " | ".join(result["consistent_with_evidence"]),
                     "unexplained": " | ".join(result["unexplained_or_contradicted"])})
        pd.DataFrame(rows).to_csv(ASSESSOR, index=False)
        print(f"{r.account_id}: expected {r.expected}, got {result['recommendation']}", flush=True)


# --- 4. hand-check sheets ----------------------------------------------------------------------

def _records():
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(NOTES.glob("*.json"))}


def _final_note(record):
    return record["error_log"][-1]["note"]


def stated_hold(note):
    """The hold level the note's own 'Recommended hold level' section states."""
    section = note.split("Recommended hold level", 1)[-1].split("##", 1)[0].lower()
    if re.search(r"\bnone\b|no (temporary debit )?hold", section):
        return "none"
    if "transaction" in section:
        return "transaction"
    return "account" if "account" in section else "unclear"


def sheets():
    """Citation sheet for 15 notes (10 laundering, 5 clean) and a sheet of every draft the
    verifier sent back. Judgment columns are filled in by hand."""
    records, ev = _records(), _eval_set()
    picked = list(ev[ev.label == "laundering"].account_id[:10]) + list(ev[ev.label == "clean"].account_id[:5])
    by_id = corpus.clauses_by_id()
    rows = []
    for aid in picked:
        for line in _final_note(records[aid]).splitlines():
            for group in verifier.CITATION.findall(line.translate(verifier.NORMALISE)):
                for cid in (verifier.normalise_citation(c) for c in group.split(";")):
                    rows.append({"account_id": aid, "clause_id": cid, "claim": line.strip()[:400],
                                 "clause_text": by_id.get(cid, {}).get("text", "(no such clause)")[:400],
                                 "supports": "", "comment": ""})
    pd.DataFrame(rows).to_csv(CITATION_SHEET, index=False)

    rows = []
    for aid, record in records.items():
        for entry in record["error_log"][:-1]:   # every draft except the last was sent back
            for error in entry["errors"]:
                m = re.search(r"Number ([\d.]+)|\[([^\]]+)\]", error)
                needle = (m.group(1) or m.group(2)).rstrip("0").rstrip(".") if m else ""
                context = next((l.strip() for l in entry["note"].replace(",", "").splitlines() if needle and needle in l), "")
                rows.append({"account_id": aid, "draft": entry["draft"], "error": error, "context": context[:300],
                             "real_error": "", "comment": ""})
    pd.DataFrame(rows).to_csv(VERIFIER_SHEET, index=False)
    print(f"{CITATION_SHEET.name}: fill 'supports' (yes / partly / no)\n{VERIFIER_SHEET.name}: fill 'real_error' (yes / no)")


# --- 5. report -----------------------------------------------------------------------------------

def report():
    out, lines = {}, ["# Eval results", "", f"Frozen sets: 30 accounts (20 laundering, 10 clean), 15 customer explanations. "
                      f"Case date {CASE_DATE}; model `{config.GROQ_MODEL}`.", ""]

    det = pd.read_csv(DETECTION)
    truth = (det.label == "laundering").tolist()
    for key, col in [("with_linkage", "hold_with_linkage"), ("without_linkage", "hold_without_linkage")]:
        out[f"detection_{key}"] = _metrics(truth, (det[col] != "none").tolist())
        out[f"account_hold_{key}"] = _metrics(truth, (det[col] == "account").tolist())

    records = _records()
    seconds = [r["seconds"] for r in records.values()]
    sent_back = sum(len(r["error_log"]) - 1 for r in records.values())
    flagged = [aid for aid, r in records.items() if r["flagged"]]
    holds_match = sum(stated_hold(_final_note(r)) == r["typology"]["hold_level"] for r in records.values())
    out["notes"] = {"n": len(records), "first_draft_pass": sum(r["drafts"] == 1 and not r["errors"] for r in records.values()),
                    "drafts_sent_back": sent_back, "flagged_after_retry": len(flagged), "hold_matches_rules": holds_match,
                    "seconds_mean": statistics.mean(seconds), "seconds_median": statistics.median(seconds),
                    "seconds_max": max(seconds)}

    cit = pd.read_csv(CITATION_SHEET).fillna("")
    judged = cit[cit.supports != ""]
    out["citations"] = {"n": len(cit), "notes": cit.account_id.nunique(), "exist": int((cit.clause_text != "(no such clause)").sum()),
                        "judged": len(judged), "supports_yes": int((judged.supports == "yes").sum()),
                        "supports_partly": int((judged.supports == "partly").sum()), "supports_no": int((judged.supports == "no").sum())}

    ver = pd.read_csv(VERIFIER_SHEET).fillna("")
    if len(ver):
        per_draft = ver.groupby(["account_id", "draft"]).real_error.agg(lambda s: "yes" in set(s))
        out["verifier"] = {"drafts_sent_back": len(per_draft), "with_real_error": int(per_draft.sum()),
                           "errors_flagged": len(ver), "errors_real": int((ver.real_error == "yes").sum())}
        # Re-check the same saved drafts with the current verifier (fixed after this run).
        ids, real = corpus.clauses_by_id().keys(), set(per_draft[per_draft].index)
        again, still_flagged = [], 0
        for aid, r in records.items():
            ev = {"evidence": r["evidence"], "clauses": r["clauses"]}
            for i, entry in enumerate(r["error_log"]):
                errs = verifier.check(entry["note"], ev, ids) + verifier.check_hold_level(entry["note"], r["typology"]["hold_level"])
                if errs and i < len(r["error_log"]) - 1:
                    again.append((aid, entry["draft"]) in real)
                elif errs:
                    still_flagged += 1
        out["verifier_after_fix"] = {"drafts_sent_back": len(again), "with_real_error": sum(again), "flagged": still_flagged}

    tokens = [r["tokens"]["input"] + r["tokens"]["output"] for r in records.values() if r.get("tokens")]
    out["notes"]["tokens_per_note_mean"] = statistics.mean(tokens) if tokens else None
    out["notes"]["notes_with_token_counts"] = len(tokens)

    ass = pd.read_csv(ASSESSOR)
    out["assessor"] = {"n": len(ass), "agree": int((ass.expected == ass.predicted).sum()),
                       "by_difficulty": ass.assign(ok=ass.expected == ass.predicted).groupby("difficulty").ok.agg(["sum", "count"]).to_dict("index"),
                       "wrongly_removed": int(((ass.expected == "continue") & (ass.predicted == "remove")).sum()),
                       "wrongly_continued": int(((ass.expected == "remove") & (ass.predicted == "continue")).sum())}

    def pct(x):
        return f"{x * 100:.0f}%"

    d_w, d_wo = out["detection_with_linkage"], out["detection_without_linkage"]
    a_w, a_wo = out["account_hold_with_linkage"], out["account_hold_without_linkage"]
    n, c, a = out["notes"], out["citations"], out["assessor"]
    lines += [
        "| Metric | Test set | Result |",
        "|---|---|---|",
        f"| Detection: any hold (precision / recall) | 30 accounts | with linkage {pct(d_w['precision'])} / {pct(d_w['recall'])} "
        f"({d_w['tp']} of 20 caught, {d_w['fp']} of 10 clean held); without {pct(d_wo['precision'])} / {pct(d_wo['recall'])} "
        f"({d_wo['tp']} caught, {d_wo['fp']} clean held) |",
        f"| Detection: account-level hold (precision / recall) | 30 accounts | with linkage {pct(a_w['precision'])} / {pct(a_w['recall'])}; "
        f"without {pct(a_wo['precision'])} / {pct(a_wo['recall'])} |",
        f"| Citation accuracy | {c['notes']} notes, {c['n']} citations | {c['exist']} of {c['n']} exist; of {c['judged']} hand-checked, "
        f"{c['supports_yes']} support the claim, {c['supports_partly']} partly, {c['supports_no']} do not |",
    ]
    if "verifier" in out:
        v, f = out["verifier"], out["verifier_after_fix"]
        lines.append(f"| Verifier catch rate | {n['n']} notes | As run: {v['with_real_error']} of {v['drafts_sent_back']} drafts sent back had a real error; "
                     f"{n['flagged_after_retry']} note(s) still flagged after the retry. After fixing the two verifier bugs those false alarms "
                     f"exposed, the same drafts give {f['with_real_error']} of {f['drafts_sent_back']} real, {f['flagged']} flagged (fixes informed by this set) |")
    lines += [
        f"| Explanation-assessor agreement | 15 explanations | {a['agree']} of {a['n']} ({pct(a['agree'] / a['n'])}); "
        f"{a['wrongly_removed']} would have released a mule account, {a['wrongly_continued']} kept an honest customer on hold |",
        f"| Time per case | {n['n']} accounts | mean {n['seconds_mean']:.0f}s, median {n['seconds_median']:.0f}s, max {n['seconds_max']:.0f}s "
        f"from account ID to verified note, vs {MANUAL_MINUTES} min assumed manual; "
        f"{n['tokens_per_note_mean']:,.0f} LLM tokens per note on average ({n['notes_with_token_counts']} notes measured) |",
        "",
        "Citation and verifier judgments: first pass by an AI coding assistant, recorded with reasons in "
        "[citation_review.csv](citation_review.csv) and [verifier_review.csv](verifier_review.csv) for review.",
        "",
        f"The hold level each note states matched the rules in {n['hold_matches_rules']} of {n['n']} cases; "
        f"{n['first_draft_pass']} of {n['n']} notes passed the verifier on the first draft.",
    ]
    RESULTS.write_text("\n".join(lines) + "\n\n```json\n" + json.dumps(out, indent=1, default=str) + "\n```\n", encoding="utf-8")
    print("\n".join(lines))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    commands = {"detection": detection, "notes": notes, "assessor": assess_explanations, "sheets": sheets, "report": report}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        sys.exit(__doc__)
    commands[sys.argv[1]]()


if __name__ == "__main__":
    main()
