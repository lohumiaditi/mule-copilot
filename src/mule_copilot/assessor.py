"""Explanation assessor (SOP step 4): does the customer's explanation account for the evidence?

Advisory only: it recommends remove or continue with reasons; the officer decides.
"""

import json
from typing import Literal

from pydantic import BaseModel, Field

from mule_copilot.llm import chat

SYSTEM = """You assess a customer's explanation for an account under a temporary debit hold, for a \
bank officer deciding under SOP step 4 of RBI's September 2026 draft SOP on suspected money mule accounts.

An explanation FITS when it accounts for the specific pattern that triggered the hold (the signals: \
number of senders or receivers, timing, amounts, payment formats, money in versus out, cycles, links to \
accounts already reported on NCRP) and contradicts none of the facts. Claims you cannot check are not \
contradictions, but an explanation that does not account for the triggering pattern does not fit. If \
the account sent or received money from accounts already reported on NCRP, the explanation must account \
for those transfers.

Under the SOP, a Money Mule Account is "an account used, knowingly or unknowingly, to receive, layer \
or transfer proceeds of cyber-enabled financial fraud on behalf of another person". An explanation \
that says the account received or paid money on someone else's behalf (a friend, an employer, a \
company) does not fit, however well its numbers match.

Recommend "remove" only when the explanation fits; otherwise "continue". Quote numbers only from the \
evidence. You advise; the officer decides."""


class Assessment(BaseModel):
    recommendation: Literal["remove", "continue"]
    consistent_with_evidence: list[str] = Field(description="Claims in the explanation that the evidence supports")
    unexplained_or_contradicted: list[str] = Field(description="Facts the explanation contradicts or leaves unexplained")
    reasons: str = Field(description="Two to four sentences for the officer")


def assess(explanation, evidence):
    llm = chat().with_structured_output(Assessment, method="json_schema")
    user = f"EVIDENCE:\n{json.dumps(evidence, indent=1, default=str)}\n\nCUSTOMER EXPLANATION:\n{explanation}"
    messages = [("system", SYSTEM), ("user", user)]
    try:
        return llm.invoke(messages).model_dump()
    except Exception:   # an occasional malformed structured reply; one retry, then let it raise
        return llm.invoke(messages).model_dump()
