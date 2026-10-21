"""The SOP workflow end to end with the LLM, Neo4j and embeddings stubbed out.

Checks routing, the clock's dates and the MIS log for the three ways a held case ends.
"""

from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from mule_copilot import agent, assessor, case_log, config, corpus, documents, graph_store, typology

ACCOUNT = {
    "id": "1-80000000A", "first_seen": "2022-09-01 00:00:00", "last_seen": "2022-09-10 00:00:00",
    "n_tx_in": 10, "n_tx_out": 8, "n_senders": 3, "n_receivers": 2, "total_in_usd": 1000.0, "total_out_usd": 950.0,
    "fan_in_48h": 2, "fan_in_48h_pctl": 40.0, "fan_out_48h": 2, "fan_out_48h_pctl": 40.0,
    "pass_through_ratio": 0.95, "pass_through_ratio_pctl": 99.0,
    "rapid_forward_24h": 0.1, "rapid_forward_24h_pctl": 50.0, "cycles_3_5": 2,
}
ANCHORS = typology.HOLD_ANCHORS["account"] + [typology.DEFINITION, typology.MONITORING]
NOTE = " ".join(f"[{cid}]" for cid in ANCHORS)


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIS_DB", tmp_path / "mis.sqlite")
    monkeypatch.setattr(graph_store, "account", lambda aid: ACCOUNT)
    for name in ("transfers", "transfer_summary", "cycle_paths", "reported_links"):
        monkeypatch.setattr(graph_store, name, lambda *a, **k: [])
    clauses = {cid: {"id": cid, "title": cid, "text": "", "source": "test"} for cid in ANCHORS}
    monkeypatch.setattr(corpus, "clauses_by_id", lambda: clauses)
    monkeypatch.setattr(corpus, "search", lambda *a, **k: [])
    reply = SimpleNamespace(content=NOTE, usage_metadata={"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(agent, "chat", lambda: SimpleNamespace(invoke=lambda messages: reply))
    monkeypatch.setattr(documents, "draft", lambda kind, ctx: (f"{kind} draft", [], []))
    monkeypatch.setattr(assessor, "assess", lambda text, ev: {"recommendation": "remove", "reasons": "fits",
                                                              "consistent_with_evidence": [], "unexplained_or_contradicted": []})
    graph = agent.build_graph(InMemorySaver())
    thread = {"configurable": {"thread_id": ACCOUNT["id"]}}

    def step(value):
        graph.invoke(value, thread)
        snapshot = graph.get_state(thread)
        interrupts = [i for task in snapshot.tasks for i in task.interrupts]
        return interrupts[0].value["stage"] if interrupts else None

    return step


def _steps():
    return [(e["case_date"], e["step"]) for e in case_log.events(ACCOUNT["id"])]


def test_no_explanation_then_referral_then_release_on_day_31(run):
    assert run({"account_id": ACCOUNT["id"], "date": "2026-11-02"}) == "note_review"
    assert run(Command(resume={"action": "approve", "date": "2026-11-02"})) == "awaiting_explanation"
    assert run(Command(resume={"explanation": None, "date": "2026-11-22"})) == "awaiting_decision"
    assert run(Command(resume={"action": "continue", "reason": "", "date": "2026-11-24"})) == "referral_review"
    assert run(Command(resume={"date": "2026-11-24"})) == "awaiting_lea"
    assert run(Command(resume={"instruction": None, "date": "2026-12-01"})) is None

    steps = _steps()
    assert ("2026-11-02", "Temporary debit hold placed") in steps
    assert ("2026-11-24", "STR filed with FIU-IND") in steps
    assert ("2026-12-24", "Temporary debit hold removed; customer notified") in steps   # reference + 30
    case = case_log.case(ACCOUNT["id"])
    assert (case["stage"], case["explanation_due"], case["release_on"]) == ("closed", "2026-11-22", "2026-12-24")
    kinds = [d["kind"] for d in case_log.documents(ACCOUNT["id"])]
    assert kinds == ["case_note", "customer_notice", "police_reference", "str_narrative", "continuation_notice", "removal_notice"]


def test_explanation_accepted_closes_the_case_early(run):
    run({"account_id": ACCOUNT["id"], "date": "2026-11-02"})
    run(Command(resume={"action": "approve", "date": "2026-11-02"}))
    run(Command(resume={"explanation": "I run a business.", "date": "2026-11-05"}))
    assert case_log.case(ACCOUNT["id"])["decision_due"] == "2026-11-15"   # explanation + 10
    assert run(Command(resume={"action": "remove", "reason": "fits", "date": "2026-11-06"})) is None
    assert case_log.case(ACCOUNT["id"])["outcome"] == "Hold removed: explanation accepted"
    assert ("2026-11-05", "Explanation assessed") in _steps()


def test_late_decision_and_lea_instruction_are_logged(run):
    run({"account_id": ACCOUNT["id"], "date": "2026-11-02"})
    run(Command(resume={"action": "approve", "date": "2026-11-02"}))
    run(Command(resume={"explanation": None, "date": "2026-11-22"}))
    run(Command(resume={"action": "continue", "reason": "", "date": "2026-12-05"}))   # due 2026-12-02
    run(Command(resume={"date": "2026-12-05"}))
    assert run(Command(resume={"instruction": "Freeze until further orders", "date": "2026-12-10"})) is None
    steps = _steps()
    assert ("2026-12-05", "Decision taken after the SOP deadline") in steps
    assert case_log.case(ACCOUNT["id"])["outcome"] == "Acted on LEA instruction: Freeze until further orders"


def test_rejected_note_closes_without_a_hold(run):
    run({"account_id": ACCOUNT["id"], "date": "2026-11-02"})
    assert run(Command(resume={"action": "reject", "date": "2026-11-03"})) is None
    assert case_log.case(ACCOUNT["id"])["hold_date"] is None
