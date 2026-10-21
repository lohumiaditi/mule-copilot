import pandas as pd

from mule_copilot import features
from mule_copilot.ingest import parse_patterns

T0 = pd.Timestamp("2022-09-01")


def _tx(rows):
    df = pd.DataFrame(rows, columns=["src", "dst", "hours", "amount_usd"])
    df["ts"] = T0 + pd.to_timedelta(df.hours, unit="h")
    return df.drop(columns="hours")


def test_max_distinct_in_window_counts_distinct_counterparties_only():
    times = [T0, T0 + pd.Timedelta(hours=1), T0 + pd.Timedelta(hours=2)]
    assert features.max_distinct_in_window(times, ["a", "a", "b"]) == 2


def test_max_distinct_in_window_drops_old_transfers():
    times = [T0, T0 + pd.Timedelta(hours=49), T0 + pd.Timedelta(hours=50)]
    assert features.max_distinct_in_window(times, ["a", "b", "c"]) == 2


def _h(hours):
    return T0 + pd.Timedelta(hours=hours)


def test_rapid_forward_counts_only_money_forwarded_within_24h():
    events = [(_h(0), 100.0), (_h(2), -60.0), (_h(30), -40.0)]
    assert features.rapid_forward_share(events) == 0.6


def test_rapid_forward_matches_oldest_inflow_first():
    # The hour-48 outflow spends the stale hour-0 lot first, so only 50 of the fresh 100 is fast.
    events = [(_h(0), 50.0), (_h(40), 100.0), (_h(48), -100.0)]
    assert features.rapid_forward_share(events) == 50 / 150


def test_rapid_forward_ignores_outflows_with_no_prior_inflow():
    events = [(_h(0), -500.0), (_h(1), 100.0)]
    assert features.rapid_forward_share(events) == 0.0


def test_compute_mule_like_pass_through_and_cycle():
    tx = _tx([
        ("s1", "mule", 0, 100), ("s2", "mule", 1, 100), ("s3", "mule", 2, 100),
        ("mule", "x", 3, 150), ("mule", "y", 4, 140),
        ("x", "z", 5, 10), ("z", "mule", 6, 10),       # cycle mule -> x -> z -> mule
        ("mule", "mule", 7, 999),                      # self-transfer is ignored
    ])
    accounts = pd.DataFrame({"account_id": ["s1", "s2", "s3", "mule", "x", "y", "z"], "in_scope": True, "background": True})
    f = features.compute(tx, accounts).set_index("account_id")

    assert f.loc["mule", "fan_in_48h"] == 4
    assert f.loc["mule", "fan_out_48h"] == 2
    assert f.loc["mule", "pass_through_ratio"] == round(290 / 310, 4)
    assert f.loc["mule", "cycles_3_5"] == 1
    assert f.loc["mule", "rapid_forward_24h"] == round(290 / 310, 4)
    assert f.loc["s1", "cycles_3_5"] == 0
    assert f.loc["mule", "fan_in_48h_pctl"] == round(6 / 7 * 100, 1)   # above the 6 other accounts
    assert f.loc["s1", "cycles_3_5_pctl"] == 0.0


def test_parse_patterns_normalises_bank_padding(tmp_path):
    path = tmp_path / "patterns.txt"
    path.write_text(
        "BEGIN LAUNDERING ATTEMPT - FAN-OUT:  Max 2-degree Fan-Out\n"
        "2022/09/01 00:06,021174,800737690,012,80011F990,10.00,Euro,10.00,Euro,ACH,1\n"
        "2022/09/01 04:33,021174,800737690,00220,80020C5B0,20.00,Euro,20.00,Euro,ACH,1\n"
        "END LAUNDERING ATTEMPT - FAN-OUT\n\n"
        "BEGIN LAUNDERING ATTEMPT - CYCLE:  Max 3 hops\n"
        "2022/09/02 00:00,012,80011F990,021174,800737690,5.00,Euro,5.00,Euro,ACH,1\n"
        "END LAUNDERING ATTEMPT - CYCLE\n"
    )
    p = parse_patterns(path)
    assert list(p.pattern) == ["FAN-OUT", "FAN-OUT", "CYCLE"]
    assert list(p.attempt_id) == [0, 0, 1]
    assert p.src.iloc[0] == "21174-800737690"
    assert p.dst.iloc[1] == "220-80020C5B0"
