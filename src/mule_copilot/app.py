"""Mule Copilot investigator screen.

    streamlit run src/mule_copilot/app.py

Case queue sorted by the next SOP deadline, graph view, case note, documents, MIS log,
and an action panel for whichever pause the case is waiting at. Dates are simulated:
pick "today" in the sidebar to move the calendar.
"""

from datetime import date, timedelta

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

from mule_copilot import agent, case_log, clock, config, graph_store

st.set_page_config(page_title="Mule Copilot", layout="wide")

STAGES = {
    "investigation": "Copilot investigating",
    "note_review": "Officer: review case note",
    "awaiting_explanation": "Waiting for customer explanation",
    "awaiting_decision": "Officer: decide (remove / continue)",
    "referral_review": "Officer: approve police reference and STR",
    "awaiting_lea": "Waiting for police instruction",
    "closed": "Closed",
}
DOC_NAMES = {"case_note": "Case note", "customer_notice": "Customer notice: hold", "police_reference": "Police reference (NCRP)",
             "str_narrative": "STR narrative (FIU-IND)", "continuation_notice": "Customer notice: hold continues",
             "removal_notice": "Customer notice: hold removed"}


@st.cache_data
def eval_accounts():
    return pd.read_csv(config.EVAL_ACCOUNTS).account_id.tolist()


@st.cache_data
def sample_explanations():
    if not config.EXPLANATIONS.exists():
        return {}
    return {r["account_id"]: r for r in pd.read_csv(config.EXPLANATIONS).to_dict("records")}


def _date(value):
    return date.fromisoformat(value) if value else None


def queue_rows(today):
    rows = []
    for c in case_log.cases():
        if c["stage"] == "note_review":
            nxt = ("Review case note", _date(c["opened_on"]))
        elif c["stage"] == "referral_review":
            nxt = ("Approve referral", _date(c["decision_due"]))
        else:
            dl = {"explanation_due": c["explanation_due"], "decision_due": c["decision_due"], "release_on": c["release_on"]}
            found = clock.next_deadline(c["stage"], dl, today)
            nxt = found[:2] if found else (None, None)
        rows.append({"Case": c["case_id"], "Stage": STAGES.get(c["stage"], c["stage"]), "Hold": c["hold_level"],
                     "Next": nxt[0], "Due": nxt[1], "Days left": (nxt[1] - today).days if nxt[1] else None,
                     "Outcome": c["outcome"]})
    df = pd.DataFrame(rows)
    if not df.empty:
        df["_closed"] = df.Stage == STAGES["closed"]
        df = df.sort_values(["_closed", "Due"], na_position="last").drop(columns="_closed").reset_index(drop=True)
    return df


@st.cache_data(show_spinner=False)
def graph_html(account_id, cycles):
    """Counterparty graph, built once per account: Streamlit reruns every tab on every click."""
    net = Network(height="460px", width="100%", directed=True, cdn_resources="in_line", bgcolor="#ffffff")
    net.add_node(account_id, label=account_id, color="#d62728", size=28, title="Account under review")
    for cp in graph_store.counterparties(account_id):
        color = "#8c1c13" if cp["reported"] else "#7f7f7f"
        title = f"{cp['id']}\n{cp['transfers']} transfers, USD {cp['amount_usd']:,.2f}" + ("\nReported on NCRP" if cp["reported"] else "")
        net.add_node(cp["id"], label=cp["id"], color=color, size=12, title=title)
        a, b = (account_id, cp["id"]) if cp["direction"] == "out" else (cp["id"], account_id)
        net.add_edge(a, b, value=cp["amount_usd"], title=f"{cp['transfers']} x, USD {cp['amount_usd']:,.2f}")
    for path in cycles:
        for a, b in zip(path, path[1:]):
            for n in (a, b):
                if n not in net.get_nodes():
                    net.add_node(n, label=n, color="#ff7f0e", size=10, title="In a 3-5 cycle")
            net.add_edge(a, b, color="#ff7f0e", title="cycle")
    net.toggle_physics(True)
    return net.generate_html(notebook=False)


# --- sidebar ------------------------------------------------------------------------------

with st.sidebar:
    st.header("Mule Copilot")
    st.session_state.setdefault("today", date.today())
    today = st.date_input("Today (simulated)", key="today")
    st.divider()
    st.subheader("Open a case")
    account = st.selectbox("Flagged account", eval_accounts())
    linkage = st.checkbox("Use NCRP linkage indicator", value=True)
    if st.session_state.pop("clear_replace", False):   # never carry a destructive tick into the next run
        st.session_state.replace = False
    replace = st.checkbox("Replace an existing case (deletes its record)", key="replace")
    if st.button("Run copilot", type="primary"):
        st.session_state.clear_replace = True
        try:
            with st.spinner("Gathering evidence, scoring, drafting and verifying the case note..."):
                agent.open_case(account, today, use_linkage=linkage, replace=replace)
        except Exception as e:
            st.error(str(e))
        else:
            st.session_state.selected = account
            st.rerun()
    st.divider()
    st.caption("Built against RBI's September 2026 draft SOP (Annex III), not a compliance claim. "
               "Synthetic IBM data; synthetic bank.")

# --- queue --------------------------------------------------------------------------------

st.title("Case queue")
queue = queue_rows(today)
if queue.empty:
    st.info("No cases yet. Open one from the sidebar.")
    st.stop()

event = st.dataframe(queue, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                     column_config={"Due": st.column_config.DateColumn(format="D MMM YYYY"),
                                    "Days left": st.column_config.NumberColumn(help="Negative means overdue")})
if event.selection.rows:
    st.session_state.selected = queue.iloc[event.selection.rows[0]]["Case"]
aid = st.session_state.get("selected") or queue.iloc[0]["Case"]
if aid not in set(queue.Case):
    aid = queue.iloc[0]["Case"]

# --- case ---------------------------------------------------------------------------------

s = agent.state(aid)
c = case_log.case(aid)
pause = agent.pending(aid)
t = s.get("typology", {})

st.header(f"Case {aid}")
signals = ", ".join(x["name"] for x in t.get("signals", [])) or "none"
st.markdown(f"**Stage:** {STAGES.get(c['stage'], c['stage'])} &nbsp;·&nbsp; **Hold:** {t.get('hold_level', '-')} "
            f"&nbsp;·&nbsp; **Signals:** {signals}  \n**Hold placed:** {c['hold_date'] or '-'} &nbsp;·&nbsp; "
            f"**Explanation due:** {c['explanation_due'] or '-'} &nbsp;·&nbsp; **Decision due:** {c['decision_due'] or '-'} "
            f"&nbsp;·&nbsp; **Referred:** {c['reference_date'] or '-'} &nbsp;·&nbsp; **Release on:** {c['release_on'] or '-'}")

case_day = _date(s.get("date"))
default_day = max(today, case_day or today)


def day_input(label, key, default):
    """Date input keyed per case and stage, so values don't leak between cases. A step can't be
    dated before the case's latest step, or the SOP clock and the MIS log would run backwards."""
    key = f"{key}_{aid}"
    st.session_state.setdefault(key, default)
    return st.date_input(label, key=key, min_value=case_day)


def act(step, *args):
    """Run a workflow call; a failure leaves the case at its last checkpoint, ready to retry."""
    try:
        with st.spinner("Working..."):
            step(aid, *args)
    except Exception as e:
        st.session_state.failed = f"{type(e).__name__}: {e}"
    st.rerun()


if pause is not None and (message := st.session_state.pop("failed", None)):
    st.error(message)

with st.container(border=True):
    if pause is None and c["stage"] == "closed":
        st.subheader("Closed")
        st.write(c["outcome"] or "")
    elif pause is None and (error := st.session_state.pop("failed", None) or agent.stopped(aid)):
        st.subheader("Stopped before its next step")
        st.error(f"The case did not reach its next pause. {error}")
        st.write("Retry runs the unfinished step again from the last checkpoint; finished steps are not repeated.")
        if st.button("Retry", type="primary"):
            act(agent.retry)
    elif pause is None:   # no error: a step is still running, e.g. from another tab
        st.subheader("Running")
        st.write("This case is still working. Refresh in a moment.")
    elif pause["stage"] == "note_review":
        st.subheader("Officer: review the case note")
        if pause["flagged"]:
            st.error(f"Verifier flagged this note after a retry: {pause['verifier_errors']}")
        else:
            st.success(f"Verifier passed ({s['drafts']} draft(s)). Recommended hold: {pause['hold_level']}.")
        note = st.text_area("Case note (editable)", pause["note"], height=320)
        day = day_input("Date", "d_note", default_day)
        b = st.columns(3)
        if b[0].button("Approve", type="primary"):
            act(agent.resume, {"action": "approve" if note == pause["note"] else "edit", "note": note, "date": str(day)})
        if b[1].button("Reject"):
            act(agent.resume, {"action": "reject", "reason": "Rejected at review", "date": str(day)})
    elif pause["stage"] == "awaiting_explanation":
        st.subheader("Customer explanation")
        st.write(f"Hold placed {s['hold_date']}. Explanation due **{pause['explanation_due']}** (SOP Annex III para 5 step 3).")
        sample = sample_explanations().get(aid)
        if sample is not None and st.button("Load sample explanation"):
            st.session_state[f"expl_{aid}"] = sample["explanation"]
            st.session_state[f"d_expl_{aid}"] = _date(s["hold_date"]) + timedelta(days=int(sample["received_day"]))
        text = st.text_area("Explanation received from the customer", key=f"expl_{aid}", height=140)
        day = day_input("Date received", "d_expl", default_day)
        b = st.columns(3)
        if b[0].button("Record explanation", type="primary", disabled=not text.strip()):
            act(agent.resume, {"explanation": text, "date": str(day)})
        if b[1].button("No explanation received"):
            act(agent.resume, {"explanation": None, "date": str(max(day, _date(pause["explanation_due"])))})
    elif pause["stage"] == "awaiting_decision":
        st.subheader("Officer: decide")
        st.write(f"Decision due **{pause['decision_due']}** (SOP Annex III para 5 step 4).")
        if pause.get("explanation"):
            st.markdown(f"> {pause['explanation']}")
        a = pause.get("assessment")
        if a:
            (st.success if a["recommendation"] == "remove" else st.warning)(f"Copilot recommends: **{a['recommendation']}**. {a['reasons']}")
            left, right = st.columns(2)
            left.markdown("**Consistent with evidence**\n" + "\n".join(f"- {x}" for x in a["consistent_with_evidence"]))
            right.markdown("**Unexplained or contradicted**\n" + "\n".join(f"- {x}" for x in a["unexplained_or_contradicted"]))
        else:
            st.warning("No explanation was received. Decide on due diligence.")
        reason = st.text_input("Reason for the decision")
        day = day_input("Date", "d_dec", default_day)
        b = st.columns(3)
        if b[0].button("Remove hold"):
            act(agent.resume, {"action": "remove", "reason": reason, "date": str(day)})
        if b[1].button("Continue hold and refer to police", type="primary"):
            act(agent.resume, {"action": "continue", "reason": reason, "date": str(day)})
    elif pause["stage"] == "referral_review":
        st.subheader("Officer: approve the police reference and STR")
        errs = pause.get("doc_errors", {})
        left, right = st.columns(2)
        police = left.text_area("Police reference (NCRP-CFCFRMS)", pause["police_reference"], height=360)
        left.caption(f"Verifier: {errs.get('police_reference') or 'passed'}")
        narrative = right.text_area("STR narrative (FIU-IND)", pause["str_narrative"], height=360)
        right.caption(f"Verifier: {errs.get('str_narrative') or 'passed'}")
        day = day_input("Date", "d_ref", default_day)
        if st.button("Approve, refer and file", type="primary"):
            act(agent.resume, {"police_reference": police, "str_narrative": narrative, "date": str(day)})
    elif pause["stage"] == "awaiting_lea":
        st.subheader("Police instruction")
        st.write(f"Without an instruction the hold is removed on **{pause['release_on']}** (SOP Annex III para 5 step 6).")
        text = st.text_area("Instruction received from the police / competent authority", height=100)
        day = day_input("Date received", "d_lea", default_day)
        b = st.columns(3)
        if b[0].button("Record instruction", disabled=not text.strip()):
            act(agent.resume, {"instruction": text, "date": str(day)})
        if b[1].button("No instruction: release on schedule", type="primary"):
            act(agent.resume, {"instruction": None, "date": pause["release_on"]})

tabs = st.tabs(["Case note", "Graph", "Documents", "MIS log", "Evidence"])
with tabs[0]:
    notes = [d for d in case_log.documents(aid) if d["kind"] == "case_note"]
    st.markdown(notes[-1]["content"] if notes else s.get("note", ""))
    st.caption(agent.FOOTER.strip("_"))
with tabs[1]:
    st.caption("Red: account under review. Dark red: already reported on NCRP. Orange: 3-5 cycles. Edge width: USD amount.")
    components.html(graph_html(aid, tuple(tuple(p) for p in s.get("cycles") or [])), height=480)
with tabs[2]:
    for d in case_log.documents(aid):
        with st.expander(f"{d['case_date']}  {DOC_NAMES.get(d['kind'], d['kind'])}  (verifier: {d['verifier_errors']})"):
            st.markdown(d["content"])
with tabs[3]:
    st.dataframe(pd.DataFrame(case_log.events(aid))[["case_date", "actor", "step", "detail"]], hide_index=True,
                 width="stretch")
with tabs[4]:
    st.subheader("Typology signals")
    st.dataframe(pd.DataFrame([{"signal": x["name"], "description": x["description"], "SOP indicator": x["sop_indicator"],
                                "values": x["values"]} for x in t.get("signals", [])]), hide_index=True, width="stretch")
    st.subheader("Transfers by format")
    st.dataframe(pd.DataFrame(s.get("summary", [])), hide_index=True, width="stretch")
    st.subheader("Account facts")
    st.json(s.get("facts", {}), expanded=False)
