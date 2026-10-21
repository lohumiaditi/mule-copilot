"""Mule-risk profile for one account, read from Neo4j.

    python -m mule_copilot.risk_profile 21174-800737690
"""

import sys

from mule_copilot import graph_store

ROWS = [
    ("Fan-in, 48h (distinct senders)", "fan_in_48h"),
    ("Fan-out, 48h (distinct receivers)", "fan_out_48h"),
    ("Pass-through ratio", "pass_through_ratio"),
    ("Cycles of length 3-5", "cycles_3_5"),
    ("Transfers in", "n_tx_in"),
    ("Transfers out", "n_tx_out"),
]


def render(account_id):
    a = graph_store.account(account_id)
    if a is None:
        return f"Account {account_id} not found."
    lines = [
        f"Account {a['id']}" + ("" if a["in_scope"] else "  (boundary node: only transfers with in-scope accounts are loaded)"),
        f"Active {a['first_seen'][:16]} to {a['last_seen'][:16]}",
        f"In:  {a['n_tx_in']} transfers from {a['n_senders']} senders, USD {a['total_in_usd']:,.2f}",
        f"Out: {a['n_tx_out']} transfers to {a['n_receivers']} receivers, USD {a['total_out_usd']:,.2f}",
        f"Community {a['community_id']} ({a['community_size']} accounts)",
        "",
        f"{'Feature':<36}{'Value':>10}{'Percentile':>12}",
    ]
    for label, key in ROWS:
        lines.append(f"{label:<36}{a[key]:>10}{a[key + '_pctl']:>11}%")
    cycles = graph_store.cycle_paths(account_id, limit=3) if a["cycles_3_5"] else []
    for path in cycles:
        lines.append("Cycle: " + " -> ".join(path))
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(render(sys.argv[1]))
