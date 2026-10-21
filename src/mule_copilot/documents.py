"""Drafted documents for each SOP stage.

LLM-drafted, then verified like the case note (numbers from the evidence, clause IDs that
exist), with one retry:
    customer_notice    step 2: hold, reasons, how to get it removed, officer contact
    police_reference   step 4(b): reference to the Jurisdictional Police Authority via NCRP-CFCFRMS
    str_narrative      KYC paras 52-55: grounds of suspicion for FIU-IND

Fixed templates (no LLM): removal and continuation notices.

Customer-facing documents must never mention an STR or FIU-IND: the fact of reporting is
confidential (KYC Directions para 54), so that is checked too.
"""

import json
import re

from mule_copilot import config, verifier
from mule_copilot.corpus import clauses_by_id
from mule_copilot.llm import chat

TIPPING_OFF = re.compile(r"\bSTR\b|suspicious transaction report|\bFIU|Financial Intelligence Unit|money.laundering", re.I)

COMMON = """Rules:
- Use only the CONTEXT and CLAUSES given. Do not add facts.
- Copy numbers and dates exactly from the context; do not calculate new ones.
- Cite clauses in square brackets with their exact ID, plain text, e.g. [SOP Annex III para 4 step 2]. \
Cite only IDs listed in CLAUSES, and at least one.
- Say "suspected"; never state that the customer is a money mule or a criminal.
- Write the body only, in markdown. No letterhead, date line or signature: those are added for you."""

SPECS = {
    "customer_notice": {
        "title": "Notice of temporary debit hold",
        "customer_facing": True,
        "clauses": ["SOP Annex III para 4 step 1", "SOP Annex III para 4 step 2", "SOP Annex III para 5 step 3",
                    "SOP Annex III para 8", "SOP Annex III para 3 (Temporary Debit Hold)"],
        "task": """Write the notice to the account holder required by SOP step 2. Plain, polite English \
for a customer. Include: that a temporary debit hold has been placed (say whether on specific amounts or \
the whole account, per hold_level), the reasons in plain words based on the signals (no internal scores, \
percentiles or thresholds), how to get it removed (send an explanation with supporting documents by \
explanation_due), the officer's contact details, and how to raise a grievance. Never mention STRs, FIU-IND, \
money laundering or police.""",
    },
    "police_reference": {
        "title": "Reference to Jurisdictional Police Authority (NCRP-CFCFRMS)",
        "customer_facing": False,
        "clauses": ["SOP Annex III para 5 step 4", "SOP Annex III para 5 step 5", "SOP Annex III para 5 step 6",
                    "SOP Annex III para 3 (Jurisdictional Police Authority)", "SOP Annex III para 3 (Money Mule Account)",
                    "SOP Annex III para 3 (Suspected Money Mule Transaction)"],
        "task": """Write the bank's reference to the Jurisdictional Police Authority through NCRP-CFCFRMS under \
SOP step 4(b). Sections: ## Account and hold, ## Grounds for suspicion (each signal with its values), \
## Customer explanation and bank's assessment, ## Request (instruction within 30 days of reference; without \
one the hold is removed on release_on).""",
    },
    "str_narrative": {
        "title": "Suspicious Transaction Report: grounds of suspicion",
        "customer_facing": False,
        "clauses": ["KYC-CB-2025 para 52", "KYC-CB-2025 para 55", "KYC-CB-2025 para 5(1)(xviii)",
                    "Draft Amendment 2026 para 4(1)", "SOP Annex III para 7"],
        "task": """Write the grounds-of-suspicion narrative for an STR to FIU-IND. Sections: ## Subject account \
and period, ## Transaction pattern (counts, amounts, formats, counterparties), ## Red flags (only the \
signals in the context, nothing of your own), ## Customer explanation and why it does not resolve the \
suspicion, ## Action taken by the bank (hold, police reference).""",
    },
}


def _header(kind, ctx):
    return (f"**{config.BANK_NAME}**  \n**{SPECS[kind]['title']}**  \n"
            f"Date: {ctx['date']}  \nAccount: {ctx['account']['id']}  \nCase reference: MC-{ctx['account']['id']}\n\n")


def check(kind, body, ctx, clauses):
    errors = verifier.check(body, {"context": ctx, "clauses": clauses}, clauses_by_id().keys())
    if SPECS[kind]["customer_facing"]:
        errors += verifier.check_hold_level(body, ctx["hold_level"])
        if m := TIPPING_OFF.search(body):
            errors.append(f"Customer document mentions '{m.group(0)}' (reporting must stay confidential).")
    return errors


def draft(kind, ctx):
    """Draft, verify, retry once. Returns (document text, final errors, error log)."""
    spec = SPECS[kind]
    by_id = clauses_by_id()
    clauses = [{k: by_id[cid][k] for k in ("id", "title", "text")} for cid in spec["clauses"]]
    user = (f"CONTEXT:\n{json.dumps(ctx, separators=(',', ':'), default=str, ensure_ascii=False)}\n\n"
            f"CLAUSES:\n{json.dumps(clauses, separators=(',', ':'), ensure_ascii=False)}")
    messages = [("system", f"You draft documents for a bank's money-mule investigation desk.\n\nTASK:\n{spec['task']}\n\n{COMMON}"),
                ("user", user)]
    log = []
    for attempt in (1, 2):
        body = chat().invoke(messages).content.strip()
        errors = check(kind, body, ctx, clauses)
        log.append({"draft": attempt, "errors": errors})
        if not errors:
            break
        messages += [("assistant", body), ("user", "Your draft failed verification:\n- " + "\n- ".join(errors)
                                           + "\nRewrite the whole document and fix every problem.")]
    return _header(kind, ctx) + body, errors, log


def removal_notice(ctx, reason):
    return (_header("customer_notice", ctx).replace(SPECS["customer_notice"]["title"], "Removal of temporary debit hold")
            + f"The temporary debit hold placed on your account on {ctx['hold_date']} has been removed today. "
            f"{reason}\n\nFor any questions, contact {config.NODAL_OFFICER}.\n")


def continuation_notice(ctx):
    return (_header("customer_notice", ctx).replace(SPECS["customer_notice"]["title"], "Continuation of temporary debit hold")
            + "We have reviewed the matter and the temporary debit hold on your account continues. As the RBI "
            "procedure requires, the case has been referred to the jurisdictional police authority through the "
            f"National Cybercrime Reporting Portal. Unless the police or a competent authority instruct otherwise, "
            f"the hold will be removed on {ctx['release_on']}.\n\nFor any questions or to raise a grievance, contact "
            f"{config.NODAL_OFFICER}.\n")
