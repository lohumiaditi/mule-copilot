"""Typology rules: deterministic mule signals over an account's graph features.

Rules decide; the LLM only writes them up. Thresholds are percentiles against the
random background sample of accounts, so they are set without looking at labels.
Community size is deliberately unused (a sampling artefact, see README).

Each signal names the SOP indicator it evidences and the clauses that must be cited
for it ("anchor" clauses), so citations don't depend on retrieval alone:
    (a) unusual for / disproportionate to the account holder's declared profile
        (IBM's data has no declared profile; the bank's book is the stand-in baseline)
    (b) linkage to an account already reported as money mule or fraudulent
"""

TOP_PCTL = 95.0          # "top 5% of the bank's book"
PASS_THROUGH_MIN = 0.8   # most of the money in goes back out

DEFINITION = "SOP Annex III para 3 (Suspected Money Mule Transaction)"
MONITORING = "KYC-CB-2025 para 40"

RULES = [
    # name, description, sop indicator, test, evidence fields
    ("collection", "Many distinct senders within 48 hours", "a",
     lambda a: a["fan_in_48h_pctl"] >= TOP_PCTL, ["fan_in_48h", "fan_in_48h_pctl"]),
    ("dispersal", "Many distinct receivers within 48 hours", "a",
     lambda a: a["fan_out_48h_pctl"] >= TOP_PCTL, ["fan_out_48h", "fan_out_48h_pctl"]),
    ("pass_through", "Most money received is sent on", "a",
     lambda a: a["pass_through_ratio"] >= PASS_THROUGH_MIN and a["n_tx_in"] > 0 and a["n_tx_out"] > 0,
     ["pass_through_ratio", "total_in_usd", "total_out_usd"]),
    ("rapid_forwarding", "Money forwarded within 24 hours of arrival", "a",
     lambda a: a["rapid_forward_24h_pctl"] >= TOP_PCTL, ["rapid_forward_24h", "rapid_forward_24h_pctl"]),
    ("round_tripping", "Money returns to the account through a chain of 3 to 5 accounts", "a",
     lambda a: a["cycles_3_5"] >= 1, ["cycles_3_5"]),
]

HOLD_ANCHORS = {
    "none": [],
    "transaction": ["SOP Annex III para 4 step 1", "SOP Annex III para 4 step 2", "SOP Annex III para 5 step 3"],
    "account": ["SOP Annex III para 4 step 1", "SOP Annex III para 6(ii)", "SOP Annex III para 4 step 2",
                "SOP Annex III para 5 step 3"],
}


def score(account, reported_links=None):
    """Signals, hold level and confidence for one account.

    account: properties from graph_store.account(). reported_links: rows from
    graph_store.reported_links(), or None to switch the linkage indicator off.
    """
    signals = []
    for name, description, indicator, test, fields in RULES:
        if test(account):
            signals.append({"name": name, "description": description, "sop_indicator": indicator,
                            "values": {f: account[f] for f in fields}})
    if reported_links:
        signals.append({"name": "linkage", "description": "Transfers with accounts already reported on NCRP",
                        "sop_indicator": "b",
                        "values": {"reported_counterparties": len(reported_links),
                                   "transfers_with_reported": sum(r["transfers"] for r in reported_links)}})

    n = len(signals)
    hold = "none" if n == 0 else "transaction" if n == 1 else "account"
    anchors = HOLD_ANCHORS[hold] + ([DEFINITION, MONITORING] if n else [])
    return {
        "signals": signals,
        "n_signals": n,
        "n_rules": len(RULES) + (reported_links is not None),
        "thresholds": {"top_percentile": TOP_PCTL, "pass_through_min": PASS_THROUGH_MIN, "cycles_min": 1},
        "hold_level": hold,
        "confidence": {0: "n/a", 1: "low", 2: "medium"}.get(n, "high"),
        "anchor_clauses": anchors,
    }
