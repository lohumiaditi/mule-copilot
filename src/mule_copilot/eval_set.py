"""Pick and freeze the detection eval set: 20 laundering accounts, 10 clean.

Laundering: in-scope accounts in a labelled pattern with at least 2 laundering
transactions (one-off counterparties are not what a mule desk investigates),
stratified across pattern types.
Clean: in-scope accounts with no laundering transactions; half random, half drawn
from the busiest decile so the set includes hard negatives.

Also writes a simulated NCRP "already reported" list for the SOP's linkage indicator:
a random 30% of pattern accounts, never including an eval account. It uses labels on
purpose, standing in for earlier victim complaints; evals report results with and
without it.

    python -m mule_copilot.eval_set
"""

import pandas as pd

from mule_copilot import config

N_LAUNDERING, N_CLEAN = 20, 10
REPORTED_SHARE = 0.3


def pick(labels, feats):
    df = labels[labels.in_scope].merge(feats[["account_id", "n_tx_in", "n_tx_out"]], on="account_id")
    df["activity"] = df.n_tx_in + df.n_tx_out

    candidates = df[df.pattern_types.notna() & (df.n_laundering_tx >= 2)].copy()
    candidates["primary_pattern"] = candidates.pattern_types.str.split("|").str[0]
    shuffled = candidates.sample(frac=1, random_state=config.SEED)
    # Round-robin across pattern types so each type is represented.
    shuffled["rank"] = shuffled.groupby("primary_pattern").cumcount()
    laundering = shuffled.sort_values(["rank", "primary_pattern"]).head(N_LAUNDERING)
    laundering = laundering.assign(label="laundering", selection="pattern member, >=2 laundering tx")

    clean_pool = df[~df.is_laundering]
    busy = clean_pool[clean_pool.activity >= clean_pool.activity.quantile(0.9)]
    hard = busy.sample(N_CLEAN // 2, random_state=config.SEED).assign(selection="clean, busiest decile")
    rest = clean_pool.drop(hard.index).sample(N_CLEAN - len(hard), random_state=config.SEED)
    clean = pd.concat([hard, rest.assign(selection="clean, random")]).assign(label="clean")

    cols = ["account_id", "label", "selection", "pattern_types", "role", "n_laundering_tx", "activity"]
    return pd.concat([laundering, clean])[cols].reset_index(drop=True)


def pick_reported(labels, eval_ids):
    pool = labels[labels.in_scope & labels.pattern_types.notna() & ~labels.account_id.isin(eval_ids)]
    return pool.sample(frac=REPORTED_SHARE, random_state=config.SEED)[["account_id"]].sort_values("account_id")


def main():
    labels = pd.read_parquet(config.LABELS)
    config.EVALS.mkdir(exist_ok=True)
    if config.EVAL_ACCOUNTS.exists():
        eval_set = pd.read_csv(config.EVAL_ACCOUNTS)   # frozen: never re-pick
    else:
        eval_set = pick(labels, pd.read_parquet(config.FEATURES))
        eval_set.to_csv(config.EVAL_ACCOUNTS, index=False)
    print(eval_set.to_string())

    reported = pick_reported(labels, set(eval_set.account_id))
    reported.to_csv(config.NCRP_REPORTED, index=False)
    print(f"\nSimulated NCRP 'already reported' list: {len(reported):,} accounts -> {config.NCRP_REPORTED}")


if __name__ == "__main__":
    main()
