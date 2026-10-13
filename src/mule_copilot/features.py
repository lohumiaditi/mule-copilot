"""Mule-pattern features per account, computed on the subsampled transaction graph.

    fan_in_48h / fan_out_48h   most distinct senders / receivers in any 48-hour window
    pass_through_ratio         min(in, out) / max(in, out) in USD; 1.0 = all money in went out
    cycles_3_5                 directed simple cycles of length 3 to 5 through the account
    community_id / _size       Louvain community on the undirected transfer graph

Self-transfers (account to itself) are ignored. Percentile ranks against the random
background sample of accounts are stored as <feature>_pctl for the typology rules.

    python -m mule_copilot.features
"""

from collections import Counter, deque

import networkx as nx
import numpy as np
import pandas as pd

from mule_copilot import config

WINDOW = pd.Timedelta(hours=48)
RANKED = ["fan_in_48h", "fan_out_48h", "pass_through_ratio", "cycles_3_5", "n_tx_in", "n_tx_out"]


def max_distinct_in_window(times, others):
    """Largest number of distinct counterparties seen within any WINDOW (times sorted)."""
    window, counts, best = deque(), Counter(), 0
    for t, other in zip(times, others):
        window.append((t, other))
        counts[other] += 1
        while t - window[0][0] > WINDOW:
            _, old = window.popleft()
            counts[old] -= 1
            if not counts[old]:
                del counts[old]
        best = max(best, len(counts))
    return best


def fan_48h(tx, account_col, other_col):
    ordered = tx.sort_values("ts")
    return ordered.groupby(account_col).apply(
        lambda g: max_distinct_in_window(g.ts.tolist(), g[other_col].tolist()), include_groups=False
    )


def cycle_counts(graph, max_len=5):
    counts = Counter()
    for cycle in nx.simple_cycles(graph, length_bound=max_len):
        if len(cycle) >= 3:
            counts.update(cycle)
    return counts


def compute(tx, accounts):
    tx = tx[tx.src != tx.dst]
    feats = pd.DataFrame(index=accounts.account_id)

    feats["n_tx_in"] = tx.groupby("dst").size()
    feats["n_tx_out"] = tx.groupby("src").size()
    feats["n_senders"] = tx.groupby("dst").src.nunique()
    feats["n_receivers"] = tx.groupby("src").dst.nunique()
    feats["total_in_usd"] = tx.groupby("dst").amount_usd.sum()
    feats["total_out_usd"] = tx.groupby("src").amount_usd.sum()
    feats["fan_in_48h"] = fan_48h(tx, "dst", "src")
    feats["fan_out_48h"] = fan_48h(tx, "src", "dst")
    feats = feats.fillna(0)

    lo = feats[["total_in_usd", "total_out_usd"]].min(axis=1)
    hi = feats[["total_in_usd", "total_out_usd"]].max(axis=1)
    feats["pass_through_ratio"] = (lo / hi.where(hi > 0)).fillna(0).round(4)

    first, last = tx.groupby("src").ts.agg(["min", "max"]), tx.groupby("dst").ts.agg(["min", "max"])
    feats["first_seen"] = pd.concat([first["min"], last["min"]], axis=1).min(axis=1)
    feats["last_seen"] = pd.concat([first["max"], last["max"]], axis=1).max(axis=1)

    directed = nx.DiGraph()
    directed.add_edges_from(tx[["src", "dst"]].drop_duplicates().itertuples(index=False))
    feats["cycles_3_5"] = pd.Series(cycle_counts(directed))
    feats["cycles_3_5"] = feats.cycles_3_5.fillna(0).astype(int)

    pairs = pd.DataFrame(np.sort(tx[["src", "dst"]].to_numpy(), axis=1), columns=["a", "b"]).value_counts()
    undirected = nx.Graph()
    undirected.add_weighted_edges_from((a, b, w) for (a, b), w in pairs.items())
    communities = nx.community.louvain_communities(undirected, weight="weight", seed=config.SEED)
    community_of, size_of = {}, {}
    for cid, members in enumerate(sorted(communities, key=len, reverse=True)):
        community_of.update(dict.fromkeys(members, cid))
        size_of[cid] = len(members)
    feats["community_id"] = feats.index.map(community_of)
    feats["community_size"] = feats.community_id.map(size_of)

    int_cols = ["n_tx_in", "n_tx_out", "n_senders", "n_receivers", "fan_in_48h", "fan_out_48h", "community_id", "community_size"]
    feats[int_cols] = feats[int_cols].fillna(-1).astype(int)
    feats[["total_in_usd", "total_out_usd"]] = feats[["total_in_usd", "total_out_usd"]].round(2)

    flags = accounts.set_index("account_id")
    feats["in_scope"] = flags.in_scope
    background = feats[flags.background]
    for col in RANKED:
        ranked = np.sort(background[col].to_numpy())
        # Share of background accounts strictly below: a 0 on a mostly-zero feature ranks 0, not 100.
        feats[f"{col}_pctl"] = (np.searchsorted(ranked, feats[col].to_numpy(), side="left") / len(ranked) * 100).round(1)
    return feats.reset_index()


def main():
    tx = pd.read_parquet(config.TRANSACTIONS)
    accounts = pd.read_parquet(config.LABELS)[["account_id", "in_scope", "background"]]
    feats = compute(tx, accounts)
    feats.to_parquet(config.FEATURES, index=False)
    print(f"Features for {len(feats):,} accounts written to {config.FEATURES}")
    print(feats[feats.in_scope].describe().T[["mean", "50%", "max"]].round(2))


if __name__ == "__main__":
    main()
