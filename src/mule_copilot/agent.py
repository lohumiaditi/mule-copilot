"""LangGraph workflow for one suspected mule case, from flag to closure, under the draft SOP.

    intake -> evidence -> typology -> retrieve -> draft <-> verify -> [officer: note review]
        no hold / rejected  -> close
        hold                -> place_hold -> customer_notice -> [customer: explanation by day 20]
                            -> assess (if explained) -> [officer: decision by day 10 / day 30]
            remove          -> release
            continue        -> referral_drafts -> [officer: approve police reference + STR] -> refer
                            -> [LEA: instruction within 30 days] -> comply | release on the 31st day

Square brackets are pauses (LangGraph interrupt). The case resumes from a SQLite checkpoint,
so weeks can pass between steps. Every resume carries the case date it happens on; the SOP
clock (clock.py) turns those dates into deadlines, and every step lands in the MIS log
(case_log.py).

Typology rules decide the hold; the LLM drafts the note and documents and assesses the
explanation; the officer decides.

    python -m mule_copilot.agent 223-811EDA940               # interactive, prompts at each pause
    python -m mule_copilot.agent 223-811EDA940 --auto        # scripted run through the whole SOP
    python -m mule_copilot.agent 223-811EDA940 --note-only   # stop at the case note (evals)
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import date, timedelta
from functools import cache
from typing import TypedDict

import pandas as pd
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from mule_copilot import assessor, case_log, clock, config, corpus, documents, graph_store, typology, verifier
from mule_copilot.llm import chat

MAX_DRAFTS = 2
MAX_CLAUSES = 10
NOTES_DIR = config.DATA_PROCESSED / "notes"
FOOTER = "_Draft for officer review. Built against RBI's September 2026 draft SOP; not a compliance determination._"

FACTS = ["id", "first_seen", "last_seen", "n_tx_in", "n_tx_out", "n_senders", "n_receivers",
         "total_in_usd", "total_out_usd", "fan_in_48h", "fan_in_48h_pctl", "fan_out_48h", "fan_out_48h_pctl",
         "pass_through_ratio", "pass_through_ratio_pctl", "rapid_forward_24h", "rapid_forward_24h_pctl", "cycles_3_5"]

SIGNAL_QUERIES = {
    "collection": "many deposits from different senders, unusual pattern inconsistent with expected activity",
    "dispersal": "funds dispersed to many accounts, layering of proceeds of fraud",
    "pass_through": "account used to receive and transfer proceeds of fraud on behalf of another person",
    "rapid_forwarding": "money received and quickly transferred out, high account turnover",
    "round_tripping": "transactions of unusual or unjustified complexity with no economic rationale",
    "linkage": "linkage to an account already reported as money mule or fraudulent",
}
NO_SIGNAL_QUERY = "ongoing due diligence, transactions consistent with the customer's profile"

SYSTEM = """You draft case notes for a bank's money-mule investigation officer, under RBI's \
September 2026 draft SOP on suspected money mule accounts (Annex III of the draft KYC Amendment \
Directions, 2026).

Rules:
- Use only the EVIDENCE and CLAUSES given. Do not add facts.
- Copy every number exactly as it appears in the evidence or clauses. Do not calculate new numbers \
(no sums, differences, averages or counts of your own). You may write a ratio such as 0.8149 as 81.49%.
- Cite clauses in square brackets with their exact ID, plain text, e.g. [SOP Annex III para 4 step 1]. \
No bold inside the brackets; a citation without square brackets does not count. Cite only IDs \
listed in CLAUSES, and cite every ID in typology.anchor_clauses at least once. Cite at least one \
clause even when there are no signals.
- Say "suspected"; never state that the account is a mule. The officer decides.
- The hold level and confidence come from typology. Do not change them.
- Only typology.signals are red flags. Other facts may go under Evidence but must not be called \
red flags or suspicious. If typology.n_signals is 0, write "None triggered by the rules" under \
Patterns matched and under Red flags, then list each check with its value and the threshold from \
typology.thresholds, and recommend no hold.

Write markdown with exactly these sections and nothing after them:
## Account summary
## Evidence
## Patterns matched
## Red flags and SOP basis
## Recommended hold level
## Confidence"""


class CaseState(TypedDict, total=False):
    account_id: str
    use_linkage: bool
    date: str                 # case date of the latest step (simulated calendar)
    stage: str
    facts: dict
    transfers: list
    summary: list
    cycles: list
    reported: list | None
    typology: dict
    clauses: list
    note: str
    errors: list
    error_log: list           # verifier errors per draft, for the verifier catch-rate eval
    drafts: int
    tokens: dict              # LLM tokens spent on the case note (input, output)
    flagged: bool
    decision: dict            # officer's note review
    hold_date: str
    deadlines: dict
    explanation: str | None
    explanation_date: str | None
    assessment: dict | None
    sop_decision: dict
    police_reference: str
    str_narrative: str
    doc_errors: dict
    reference_date: str
    lea_instruction: str | None


# --- helpers ------------------------------------------------------------------------

def _deadlines(state, reference_date=None):
    dl = clock.deadlines(state["hold_date"], state.get("explanation_date"), reference_date or state.get("reference_date"))
    return {k: v.isoformat() for k, v in dl.items()}


def _evidence(state):
    return {
        "account": state["facts"],
        "typology": state["typology"],
        "recent_transfers": state["transfers"],
        "transfer_summary": state["summary"],
        "cycles": state["cycles"],
        "reported_counterparties": state["reported"] or [],
    }


def _doc_context(state, **extra):
    dl = state.get("deadlines", {})
    return {
        "date": state["date"],
        "account": state["facts"],
        "hold_level": state["typology"]["hold_level"],
        "hold_date": state.get("hold_date"),
        "signals": state["typology"]["signals"],
        "explanation_due": dl.get("explanation_due"),
        "release_on": dl.get("release_on"),
        "officer_contact": config.NODAL_OFFICER,
        "transfer_summary": state["summary"],
        "reported_counterparties": state["reported"] or [],
        "customer_explanation": state.get("explanation"),
        "assessment": state.get("assessment"),
        "officer_decision": state.get("sop_decision"),
        **extra,
    }


def _log(state, step, actor, detail="", day=None):
    case_log.log(state["account_id"], day or state["date"], step, actor, detail)


# --- investigation --------------------------------------------------------------------

def intake(state):
    account = graph_store.account(state["account_id"])
    if account is None:
        raise ValueError(f"Account {state['account_id']} not found in the graph.")
    day = state.get("date") or date.today().isoformat()
    case_log.update_case(state["account_id"], account_id=state["account_id"], opened_on=day, stage="investigation")
    case_log.log(state["account_id"], day, "Case opened", "system", "Account flagged by transaction monitoring")
    return {"facts": {k: account[k] for k in FACTS}, "date": day, "stage": "investigation",
            "drafts": 0, "errors": [], "error_log": [], "flagged": False, "tokens": {"input": 0, "output": 0}}


def evidence(state):
    aid = state["account_id"]
    return {
        "transfers": graph_store.transfers(aid, limit=15),
        "summary": graph_store.transfer_summary(aid),
        "cycles": graph_store.cycle_paths(aid, limit=3) if state["facts"]["cycles_3_5"] else [],
        "reported": graph_store.reported_links(aid) if state.get("use_linkage", True) else None,
    }


def score(state):
    return {"typology": typology.score(state["facts"], state["reported"])}


def retrieve(state):
    t = state["typology"]
    by_id = corpus.clauses_by_id()
    picked = {cid: by_id[cid] for cid in t["anchor_clauses"]}
    queries = [SIGNAL_QUERIES[s["name"]] for s in t["signals"]] or [NO_SIGNAL_QUERY]
    for query in queries:
        for hit in corpus.search(query, k=2):
            picked.setdefault(hit["id"], by_id[hit["id"]])
    clauses = [{k: c[k] for k in ("id", "title", "text", "source")} for c in picked.values()]
    return {"clauses": clauses[:MAX_CLAUSES]}


def compact(obj):
    """Minified JSON for prompts: the same facts in about a third fewer tokens."""
    return json.dumps(obj, separators=(",", ":"), default=str, ensure_ascii=False)


def draft(state):
    user = f"EVIDENCE:\n{compact(_evidence(state))}\n\nCLAUSES:\n{compact(state['clauses'])}"
    messages = [("system", SYSTEM), ("user", user)]
    if state.get("errors"):
        messages += [("assistant", state["note"]),
                     ("user", "Your draft failed verification:\n- " + "\n- ".join(state["errors"])
                      + "\nRewrite the whole note and fix every problem.")]
    reply = chat().invoke(messages)
    usage = reply.usage_metadata or {}
    spent = state.get("tokens") or {"input": 0, "output": 0}   # cases checkpointed before token counting
    tokens = {"input": spent["input"] + usage.get("input_tokens", 0),
              "output": spent["output"] + usage.get("output_tokens", 0)}
    return {"note": reply.content.strip(), "drafts": state["drafts"] + 1, "tokens": tokens}


def verify(state):
    errors = verifier.check(state["note"], {"evidence": _evidence(state), "clauses": state["clauses"]},
                            corpus.clauses_by_id().keys())
    errors += verifier.check_hold_level(state["note"], state["typology"]["hold_level"])
    log = state["error_log"] + [{"draft": state["drafts"], "note": state["note"], "errors": errors}]
    return {"errors": errors, "error_log": log, "flagged": bool(errors) and state["drafts"] >= MAX_DRAFTS}


def after_verify(state):
    return "draft" if state["errors"] and state["drafts"] < MAX_DRAFTS else "review"


# Code before interrupt() runs again on resume, so it must be idempotent; logging goes after.

def review(state):
    aid = state["account_id"]
    case_log.update_case(aid, stage="note_review", hold_level=state["typology"]["hold_level"])
    reply = interrupt({"stage": "note_review", "note": state["note"], "hold_level": state["typology"]["hold_level"],
                       "verifier_errors": state["errors"], "flagged": state["flagged"]})
    day = reply.get("date", state["date"])
    note = reply["note"] if reply["action"] == "edit" and reply.get("note") else state["note"]
    case_log.save_document(aid, "case_note", day, note, state["errors"])
    done = {"approve": "approved", "edit": "edited and approved", "reject": "rejected"}[reply["action"]]
    case_log.log(aid, day, f"Case note {done}", "officer", reply.get("reason", ""))
    return {"decision": reply, "note": note, "date": day}


def after_review(state):
    if state["decision"]["action"] == "reject" or state["typology"]["hold_level"] == "none":
        return "close"
    return "place_hold"


def close(state):
    outcome = ("Case note rejected by officer" if state["decision"]["action"] == "reject"
               else "Cleared at triage: no typology signal, no hold")
    case_log.update_case(state["account_id"], stage="closed", closed_on=state["date"], outcome=outcome)
    _log(state, "Case closed", "system", outcome)
    return {"stage": "closed"}


# --- SOP: hold, notice, explanation, decision -----------------------------------------------

def place_hold(state):
    state = {**state, "hold_date": state["date"]}
    dl = _deadlines(state)
    level = state["typology"]["hold_level"]
    case_log.update_case(state["account_id"], stage="awaiting_explanation", hold_date=state["date"],
                         explanation_due=dl["explanation_due"], decision_due=dl["decision_due"])
    _log(state, "Temporary debit hold placed", "system",
         f"{level}-level hold [SOP Annex III para 4 step 1]" + ("; last resort [SOP Annex III para 6(ii)]" if level == "account" else ""))
    return {"hold_date": state["date"], "deadlines": dl}


def customer_notice(state):
    text, errors, _ = documents.draft("customer_notice", _doc_context(state))
    case_log.save_document(state["account_id"], "customer_notice", state["date"], text, errors)
    _log(state, "Customer notified of hold", "system",
         "Digital mode, same day [SOP Annex III para 4 step 2]" + (f"; verifier: {errors}" if errors else ""))
    return {"stage": "awaiting_explanation"}


def await_explanation(state):
    aid, dl = state["account_id"], state["deadlines"]
    case_log.update_case(aid, stage="awaiting_explanation")
    reply = interrupt({"stage": "awaiting_explanation", "explanation_due": dl["explanation_due"]})
    day = reply["date"]
    text = (reply.get("explanation") or "").strip() or None
    if text:
        case_log.log(aid, day, "Explanation received", "customer", text)
        if day > dl["explanation_due"]:
            case_log.log(aid, day, "Explanation arrived after the 20-day window", "system", f"due {dl['explanation_due']}")
    else:
        case_log.log(aid, day, "No explanation received", "system", f"due {dl['explanation_due']}")
    new = {"explanation": text, "explanation_date": day if text else None, "date": day}
    dl = _deadlines({**state, **new})
    case_log.update_case(aid, stage="awaiting_decision", decision_due=dl["decision_due"])
    return {**new, "deadlines": dl, "stage": "awaiting_decision"}


def after_explanation(state):
    return "assess" if state.get("explanation") else "officer_decision"


def assess(state):
    result = assessor.assess(state["explanation"], _evidence(state))
    _log(state, "Explanation assessed", "copilot", f"Recommends {result['recommendation']}: {result['reasons']}")
    return {"assessment": result}


def officer_decision(state):
    aid, dl = state["account_id"], state["deadlines"]
    case_log.update_case(aid, stage="awaiting_decision")
    reply = interrupt({"stage": "awaiting_decision", "decision_due": dl["decision_due"],
                       "explanation": state.get("explanation"), "assessment": state.get("assessment")})
    day = reply["date"]
    if day > dl["decision_due"]:
        case_log.log(aid, day, "Decision taken after the SOP deadline", "system", f"due {dl['decision_due']}")
    what = "remove hold" if reply["action"] == "remove" else "continue hold and refer to police"
    case_log.log(aid, day, f"Bank decision: {what}", "officer", reply.get("reason", "") + " [SOP Annex III para 5 step 4]")
    return {"sop_decision": reply, "date": day}


def after_decision(state):
    return "release" if state["sop_decision"]["action"] == "remove" else "referral_drafts"


# --- SOP: police reference, STR, LEA window ---------------------------------------------------

def referral_drafts(state):
    dl = _deadlines(state, reference_date=state["date"])
    ctx = _doc_context({**state, "deadlines": dl}, reference_date=state["date"])
    police, police_errors, _ = documents.draft("police_reference", ctx)
    narrative, str_errors, _ = documents.draft("str_narrative", ctx)
    return {"police_reference": police, "str_narrative": narrative,
            "doc_errors": {"police_reference": police_errors, "str_narrative": str_errors}}


def referral_review(state):
    case_log.update_case(state["account_id"], stage="referral_review")
    reply = interrupt({"stage": "referral_review", "police_reference": state["police_reference"],
                       "str_narrative": state["str_narrative"], "doc_errors": state["doc_errors"]})
    return {"police_reference": reply.get("police_reference") or state["police_reference"],
            "str_narrative": reply.get("str_narrative") or state["str_narrative"],
            "date": reply.get("date", state["date"])}


def refer(state):
    aid, day = state["account_id"], state["date"]
    dl = _deadlines(state, reference_date=day)
    case_log.save_document(aid, "police_reference", day, state["police_reference"], state["doc_errors"]["police_reference"])
    case_log.save_document(aid, "str_narrative", day, state["str_narrative"], state["doc_errors"]["str_narrative"])
    _log(state, "Referred to Jurisdictional Police Authority via NCRP-CFCFRMS", "officer", "[SOP Annex III para 5 step 4]")
    _log(state, "STR filed with FIU-IND", "officer", "[KYC-CB-2025 para 52]; not disclosed to customer")
    notice = documents.continuation_notice(_doc_context({**state, "deadlines": dl}))
    case_log.save_document(aid, "continuation_notice", day, notice)
    _log(state, "Customer notified: hold continues", "system", f"release on {dl['release_on']} unless LEA instructs")
    case_log.update_case(aid, stage="awaiting_lea", reference_date=day, release_on=dl["release_on"])
    return {"reference_date": day, "deadlines": dl, "stage": "awaiting_lea"}


def await_lea(state):
    aid, dl = state["account_id"], state["deadlines"]
    case_log.update_case(aid, stage="awaiting_lea")
    reply = interrupt({"stage": "awaiting_lea", "release_on": dl["release_on"]})
    instruction = (reply.get("instruction") or "").strip() or None
    if instruction:
        case_log.log(aid, reply["date"], "Instruction received from LEA", "LEA", instruction)
        return {"lea_instruction": instruction, "date": reply["date"]}
    case_log.log(aid, dl["release_on"], "No LEA instruction within 30 days of reference", "system", "[SOP Annex III para 5 step 6]")
    return {"lea_instruction": None, "date": dl["release_on"]}


def after_lea(state):
    return "comply" if state.get("lea_instruction") else "release"


def comply(state):
    _log(state, "Acted on LEA instruction; customer notified", "officer", "[SOP Annex III para 5 step 5]")
    case_log.update_case(state["account_id"], stage="closed", closed_on=state["date"],
                         outcome=f"Acted on LEA instruction: {state['lea_instruction']}")
    return {"stage": "closed"}


def release(state):
    if state.get("reference_date"):
        reason, basis = "No instruction was received from the police within 30 days of the bank's reference.", "step 6"
        outcome = "Hold removed: no LEA instruction within 30 days"
    else:
        reason, basis = "We have reviewed your explanation and are satisfied with it.", "step 4"
        outcome = "Hold removed: explanation accepted"
    notice = documents.removal_notice(_doc_context(state), reason)
    case_log.save_document(state["account_id"], "removal_notice", state["date"], notice)
    _log(state, "Temporary debit hold removed; customer notified", "system", f"[SOP Annex III para 5 {basis}]")
    case_log.update_case(state["account_id"], stage="closed", closed_on=state["date"], outcome=outcome)
    return {"stage": "closed"}


# --- graph and API --------------------------------------------------------------------------

NODES = [intake, evidence, score, retrieve, draft, verify, review, close, place_hold, customer_notice,
         await_explanation, assess, officer_decision, referral_drafts, referral_review, refer, await_lea,
         comply, release]


def build_graph(checkpointer):
    g = StateGraph(CaseState)
    for fn in NODES:
        g.add_node("typology" if fn is score else fn.__name__, fn)
    for a, b in [(START, "intake"), ("intake", "evidence"), ("evidence", "typology"), ("typology", "retrieve"),
                 ("retrieve", "draft"), ("draft", "verify"), ("close", END), ("place_hold", "customer_notice"),
                 ("customer_notice", "await_explanation"), ("assess", "officer_decision"),
                 ("referral_drafts", "referral_review"), ("referral_review", "refer"), ("refer", "await_lea"),
                 ("comply", END), ("release", END)]:
        g.add_edge(a, b)
    g.add_conditional_edges("verify", after_verify, ["draft", "review"])
    g.add_conditional_edges("review", after_review, ["close", "place_hold"])
    g.add_conditional_edges("await_explanation", after_explanation, ["assess", "officer_decision"])
    g.add_conditional_edges("officer_decision", after_decision, ["release", "referral_drafts"])
    g.add_conditional_edges("await_lea", after_lea, ["comply", "release"])
    return g.compile(checkpointer=checkpointer)


@cache
def graph():
    config.CHECKPOINTS_DB.parent.mkdir(parents=True, exist_ok=True)
    return build_graph(SqliteSaver(sqlite3.connect(config.CHECKPOINTS_DB, check_same_thread=False)))


def _thread(account_id):
    return {"configurable": {"thread_id": account_id}}


def pending(account_id):
    """The pause the case is waiting at (its payload), or None if running or finished."""
    snapshot = graph().get_state(_thread(account_id))
    interrupts = [i for task in snapshot.tasks for i in task.interrupts]
    return interrupts[0].value if interrupts else None


def state(account_id):
    return graph().get_state(_thread(account_id)).values


def open_case(account_id, day, use_linkage=True, replace=False):
    """Start a case and run it to the officer's note review.

    An existing case is never overwritten by accident: its MIS record must be kept (SOP
    para 7). Only a case that failed in its first step, or an explicit replace=True,
    starts over.
    """
    existing = case_log.case(account_id)
    if existing and existing["stage"] != "investigation" and not replace:
        raise ValueError(f"Account {account_id} already has a case ({existing['stage']}). "
                         "Its record is kept; replace it only on purpose.")
    graph().checkpointer.delete_thread(account_id)
    case_log.reset(account_id)
    start = time.time()
    graph().invoke({"account_id": account_id, "use_linkage": use_linkage, "date": str(day)}, _thread(account_id))
    save_note(state(account_id), time.time() - start)
    return pending(account_id)


def resume(account_id, reply):
    graph().invoke(Command(resume=reply), _thread(account_id))
    return pending(account_id)


def stopped(account_id):
    """The error of a step that failed (e.g. a Groq rate limit), or None."""
    tasks = graph().get_state(_thread(account_id)).tasks
    return next((t.error for t in tasks if t.error), None)


def retry(account_id):
    """Run a case's failed step again from its last checkpoint; finished steps are not repeated."""
    graph().invoke(None, _thread(account_id))
    return pending(account_id)


def save_note(s, seconds):
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    aid = s["account_id"]
    status = "FLAGGED: failed verification after retry" if s["flagged"] else "passed verification"
    header = f"# Case note: account {aid}\n\n_Verifier: {status} ({s['drafts']} draft(s))._\n\n"
    path = NOTES_DIR / f"{aid}.md"
    path.write_text(header + s["note"] + "\n\n" + FOOTER + "\n", encoding="utf-8")
    record = {k: s.get(k) for k in ("account_id", "use_linkage", "typology", "clauses", "errors", "error_log",
                                     "drafts", "flagged", "tokens")}
    record.update(evidence=_evidence(s), seconds=round(seconds, 1))
    (NOTES_DIR / f"{aid}.json").write_text(json.dumps(record, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    return path


# --- command line ---------------------------------------------------------------------------

def _scripted_reply(stage, payload, s, explanations):
    """Replies for --auto: approve, use the sample explanation if one exists, follow the assessor."""
    day = date.fromisoformat(s["date"])
    if stage == "note_review":
        return {"action": "approve", "date": str(day)}
    if stage == "awaiting_explanation":
        sample = explanations.get(s["account_id"])
        if sample:
            return {"explanation": sample["explanation"], "date": str(date.fromisoformat(s["hold_date"]) + timedelta(days=int(sample["received_day"])))}
        return {"explanation": None, "date": payload["explanation_due"]}
    if stage == "awaiting_decision":
        rec = (payload.get("assessment") or {}).get("recommendation", "continue")
        return {"action": rec, "reason": "Following the copilot's assessment" if payload.get("assessment")
                else "No explanation received", "date": str(day + timedelta(days=2))}
    if stage == "referral_review":
        return {"date": str(day)}
    if stage == "awaiting_lea":
        return {"instruction": None, "date": payload["release_on"]}


def _ask(stage, payload, s):
    print(f"\n--- Paused at: {stage} ---")
    if stage == "note_review":
        a = input("Officer: [a]pprove / [r]eject the note? ").strip().lower()[:1]
        return {"action": "reject" if a == "r" else "approve", "date": input(f"Date [{s['date']}]: ").strip() or s["date"]}
    if stage == "awaiting_explanation":
        print(f"Explanation due {payload['explanation_due']}.")
        return {"explanation": input("Customer explanation (blank = none received): ").strip() or None,
                "date": input("Date received: ").strip() or payload["explanation_due"]}
    if stage == "awaiting_decision":
        if payload.get("assessment"):
            print(json.dumps(payload["assessment"], indent=1))
        a = input(f"Officer decision by {payload['decision_due']}: [r]emove / [c]ontinue? ").strip().lower()[:1]
        return {"action": "remove" if a == "r" else "continue", "reason": input("Reason: ").strip(),
                "date": input(f"Date [{s['date']}]: ").strip() or s["date"]}
    if stage == "referral_review":
        print(payload["police_reference"], "\n\n", payload["str_narrative"])
        input("Press Enter to approve both drafts...")
        return {"date": s["date"]}
    if stage == "awaiting_lea":
        return {"instruction": input(f"LEA instruction (blank = none by {payload['release_on']}): ").strip() or None,
                "date": input("Date: ").strip() or payload["release_on"]}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("account_id")
    parser.add_argument("--date", default=date.today().isoformat(), help="case date the flag arrives on")
    parser.add_argument("--no-linkage", action="store_true")
    parser.add_argument("--replace", action="store_true", help="start over, deleting an existing case's record")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--auto", action="store_true")
    mode.add_argument("--note-only", action="store_true")
    args = parser.parse_args()

    start = time.time()
    payload = open_case(args.account_id, args.date, use_linkage=not args.no_linkage, replace=args.replace)
    s = state(args.account_id)
    print((NOTES_DIR / f"{args.account_id}.md").read_text(encoding="utf-8"))
    t = s["typology"]
    print(f"Signals: {[x['name'] for x in t['signals']] or 'none'} -> hold: {t['hold_level']} | "
          f"verifier: {s['errors'] or 'OK'} | drafts: {s['drafts']} | {time.time() - start:.1f}s to note")
    if args.note_only:
        return

    explanations = {}
    if args.auto and config.EXPLANATIONS.exists():
        explanations = {r["account_id"]: r for r in pd.read_csv(config.EXPLANATIONS).to_dict("records")}
    while payload:
        stage = payload["stage"]
        reply = _scripted_reply(stage, payload, s, explanations) if args.auto else _ask(stage, payload, s)
        payload = resume(args.account_id, reply)
        s = state(args.account_id)

    print("\nMIS log:")
    for e in case_log.events(args.account_id):
        print(f"  {e['case_date']}  {e['actor']:<8} {e['step']}  {e['detail'][:110]}")
    c = case_log.case(args.account_id)
    print(f"Outcome: {c['outcome']}")


if __name__ == "__main__":
    main()
