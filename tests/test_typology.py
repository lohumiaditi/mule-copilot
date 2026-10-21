from mule_copilot import typology

QUIET = {
    "fan_in_48h": 1, "fan_in_48h_pctl": 40.0, "fan_out_48h": 1, "fan_out_48h_pctl": 40.0,
    "pass_through_ratio": 0.1, "total_in_usd": 1000.0, "total_out_usd": 100.0, "n_tx_in": 5, "n_tx_out": 2,
    "rapid_forward_24h": 0.0, "rapid_forward_24h_pctl": 0.0, "cycles_3_5": 0,
}


def test_quiet_account_gets_no_hold():
    t = typology.score(QUIET, reported_links=[])
    assert (t["n_signals"], t["hold_level"], t["anchor_clauses"]) == (0, "none", [])
    assert t["n_rules"] == 6


def test_one_signal_means_transaction_level_hold():
    t = typology.score({**QUIET, "cycles_3_5": 2})
    assert [s["name"] for s in t["signals"]] == ["round_tripping"]
    assert t["hold_level"] == "transaction"
    assert typology.DEFINITION in t["anchor_clauses"]
    assert t["n_rules"] == 5   # linkage switched off


def test_two_signals_mean_account_level_hold_as_last_resort():
    t = typology.score({**QUIET, "pass_through_ratio": 0.9, "rapid_forward_24h_pctl": 97.0})
    assert t["hold_level"] == "account"
    assert "SOP Annex III para 6(ii)" in t["anchor_clauses"]


def test_linkage_to_reported_account_is_sop_indicator_b():
    t = typology.score(QUIET, reported_links=[{"id": "1-80000000A", "transfers": 3, "amount_usd": 10.0}])
    (signal,) = t["signals"]
    assert (signal["name"], signal["sop_indicator"]) == ("linkage", "b")
    assert signal["values"] == {"reported_counterparties": 1, "transfers_with_reported": 3}


def test_pass_through_needs_money_both_ways():
    assert typology.score({**QUIET, "pass_through_ratio": 0.95, "n_tx_out": 0})["n_signals"] == 0
