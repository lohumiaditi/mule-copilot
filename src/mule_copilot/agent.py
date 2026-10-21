"""LangGraph case-note agent: flagged account in, verified and cited case note out,
paused for the investigating officer's decision.

    intake -> evidence -> typology -> retrieve -> draft -> verify -> review (officer) -> END
                                                    ^-- errors, once --'

The typology rules decide the hold level; the LLM only writes the note. A note that
fails verification is sent back once with the errors, then flagged for the officer.

    python -m mule_copilot.agent 223-811EDA940                  # interactive review
    python -m mule_copilot.agent 223-811EDA940 --auto-approve   # batch runs and evals
    python -m mule_copilot.agent 223-811EDA940 --no-linkage     # without SOP indicator (b)
"""

import argparse
import json
import sys
import time
from typing import TypedDict

from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from mule_copilot import config, corpus, graph_store, typology, verifier

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
    facts: dict
    transfers: list
    cycles: list
    reported: list | None
    typology: dict
    clauses: list
    note: str
    errors: list
    error_log: list   # verifier errors per draft, for the verifier catch-rate eval
    drafts: int
    flagged: bool
    decision: dict


def intake(state):
    account = graph_store.account(state["account_id"])
    if account is None:
        raise ValueError(f"Account {state['account_id']} not found in the graph.")
    return {"facts": {k: account[k] for k in FACTS}, "drafts": 0, "errors": [], "error_log": [], "flagged": False}


def evidence(state):
    aid = state["account_id"]
    return {
        "transfers": graph_store.transfers(aid, limit=15),
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


def _evidence(state):
    return {
        "account": state["facts"],
        "typology": state["typology"],
        "recent_transfers": state["transfers"],
        "cycles": state["cycles"],
        "reported_counterparties": state["reported"] or [],
    }


def draft(state):
    llm = ChatGroq(model=config.GROQ_MODEL, temperature=0)
    user = (f"EVIDENCE:\n{json.dumps(_evidence(state), indent=1, default=str)}\n\n"
            f"CLAUSES:\n{json.dumps(state['clauses'], indent=1, ensure_ascii=False)}")
    messages = [("system", SYSTEM), ("user", user)]
    if state.get("errors"):
        messages += [("assistant", state["note"]),
                     ("user", "Your draft failed verification:\n- " + "\n- ".join(state["errors"])
                      + "\nRewrite the whole note and fix every problem.")]
    note = llm.invoke(messages).content.strip()
    return {"note": note, "drafts": state["drafts"] + 1}


def verify(state):
    errors = verifier.check(state["note"], {"evidence": _evidence(state), "clauses": state["clauses"]},
                            corpus.clauses_by_id().keys())
    log = state["error_log"] + [{"draft": state["drafts"], "note": state["note"], "errors": errors}]
    return {"errors": errors, "error_log": log, "flagged": bool(errors) and state["drafts"] >= MAX_DRAFTS}


def after_verify(state):
    return "draft" if state["errors"] and state["drafts"] < MAX_DRAFTS else "review"


def review(state):
    decision = interrupt({
        "account_id": state["account_id"],
        "note": state["note"],
        "hold_level": state["typology"]["hold_level"],
        "verifier_errors": state["errors"],
        "flagged": state["flagged"],
    })
    update = {"decision": decision}
    if decision.get("action") == "edit" and decision.get("note"):
        update["note"] = decision["note"]
    return update


def build_graph(checkpointer=None):
    g = StateGraph(CaseState)
    for name, fn in [("intake", intake), ("evidence", evidence), ("typology", score), ("retrieve", retrieve),
                     ("draft", draft), ("verify", verify), ("review", review)]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_edge("intake", "evidence")
    g.add_edge("evidence", "typology")
    g.add_edge("typology", "retrieve")
    g.add_edge("retrieve", "draft")
    g.add_edge("draft", "verify")
    g.add_conditional_edges("verify", after_verify, ["draft", "review"])
    g.add_edge("review", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


def _ask_officer(note_path):
    while True:
        choice = input("\nOfficer decision: [a]pprove, [e]dit, [r]eject? ").strip().lower()[:1]
        if choice == "a":
            return {"action": "approve"}
        if choice == "r":
            return {"action": "reject", "reason": input("Reason: ").strip()}
        if choice == "e":
            input(f"Edit {note_path}, save it, then press Enter...")
            return {"action": "edit", "note": note_path.read_text(encoding="utf-8")}


def save(state, seconds):
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    aid = state["account_id"]
    status = "FLAGGED: failed verification after retry" if state["flagged"] else "passed verification"
    header = f"# Case note: account {aid}\n\n_Verifier: {status} ({state['drafts']} draft(s))._\n\n"
    path = NOTES_DIR / f"{aid}.md"
    path.write_text(header + state["note"] + "\n\n" + FOOTER + "\n", encoding="utf-8")
    record = {k: state.get(k) for k in ("account_id", "use_linkage", "typology", "clauses", "errors",
                                         "error_log", "drafts", "flagged", "decision")}
    record.update(evidence=_evidence(state), seconds=round(seconds, 1))
    (NOTES_DIR / f"{aid}.json").write_text(json.dumps(record, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    return path


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("account_id")
    parser.add_argument("--no-linkage", action="store_true")
    parser.add_argument("--auto-approve", action="store_true")
    args = parser.parse_args()

    graph = build_graph()
    thread = {"configurable": {"thread_id": args.account_id}}
    start = time.time()
    state = graph.invoke({"account_id": args.account_id, "use_linkage": not args.no_linkage}, thread)
    seconds = time.time() - start
    path = save(state, seconds)

    t = state["typology"]
    print(path.read_text(encoding="utf-8"))
    print(f"Signals: {[s['name'] for s in t['signals']] or 'none'} -> hold: {t['hold_level']}")
    print(f"Verifier: {state['errors'] or 'OK'} | drafts: {state['drafts']} | {seconds:.1f}s to note")

    decision = {"action": "approve", "officer": "auto"} if args.auto_approve else _ask_officer(path)
    final = graph.invoke(Command(resume=decision), thread)
    save(final, seconds)
    print(f"Officer decision recorded: {final['decision']['action']}")


if __name__ == "__main__":
    main()
