"""Download IBM's synthetic AML data (HI-Small) and cut a demo-sized subsample.

The subsample keeps every account that appears in a labelled laundering pattern,
plus a label-blind random sample of all accounts. For each of these "core"
accounts we keep *all* of its transactions, so its features are exact. Counterparties
outside the core appear in the graph but are marked in_scope=False.

Labels (is_laundering, pattern types) go to a separate file and never into Neo4j,
so the copilot cannot read them.

    python -m mule_copilot.ingest
"""

import argparse
import re

import kagglehub
import numpy as np
import pandas as pd

from mule_copilot import config

DATASET = "ealtman2019/ibm-transactions-for-anti-money-laundering-aml"
COLUMNS = [
    "ts", "from_bank", "from_acct", "to_bank", "to_acct",
    "amount_received", "currency_received", "amount_paid", "currency_paid",
    "payment_format", "is_laundering",
]
PATTERN_HEADER = re.compile(r"^BEGIN LAUNDERING ATTEMPT - ([A-Z-]+)")


def download():
    trans = kagglehub.dataset_download(DATASET, path="HI-Small_Trans.csv")
    patterns = kagglehub.dataset_download(DATASET, path="HI-Small_Patterns.txt")
    return trans, patterns


def _with_account_ids(df):
    # Bank codes are zero-padded inconsistently ("020" vs "00220"), so normalise via int.
    df["src"] = df["from_bank"].astype(int).astype(str) + "-" + df["from_acct"]
    df["dst"] = df["to_bank"].astype(int).astype(str) + "-" + df["to_acct"]
    return df


def load_transactions(path):
    df = pd.read_csv(path, names=COLUMNS, skiprows=1, dtype={"from_bank": str, "to_bank": str, "from_acct": str, "to_acct": str})
    df["ts"] = pd.to_datetime(df["ts"], format="%Y/%m/%d %H:%M")
    return _with_account_ids(df)


def parse_patterns(path):
    rows, attempt, pattern = [], -1, None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if m := PATTERN_HEADER.match(line):
                attempt, pattern = attempt + 1, m.group(1)
            elif line and not line.startswith("END"):
                rows.append([*line.split(","), attempt, pattern])
    df = pd.DataFrame(rows, columns=COLUMNS + ["attempt_id", "pattern"])
    return _with_account_ids(df)


def usd_rates(tx):
    """Exchange rates to USD implied by the data's own cross-currency transfers."""
    paid_usd = tx[(tx.currency_paid == "US Dollar") & (tx.currency_received != "US Dollar")]
    rates = (paid_usd.amount_paid / paid_usd.amount_received).groupby(paid_usd.currency_received).median()
    received_usd = tx[(tx.currency_received == "US Dollar") & (tx.currency_paid != "US Dollar")]
    reverse = (received_usd.amount_received / received_usd.amount_paid).groupby(received_usd.currency_paid).median()
    rates = rates.combine_first(reverse)
    rates["US Dollar"] = 1.0
    return rates


def account_labels(tx, patterns):
    """Account-level ground truth: IBM's transaction labels rolled up to accounts."""
    laundering = tx[tx.is_laundering == 1]
    n_laundering = pd.concat([laundering.src, laundering.dst]).value_counts()
    types = (
        pd.concat([patterns[["src", "pattern"]].rename(columns={"src": "account_id"}),
                   patterns[["dst", "pattern"]].rename(columns={"dst": "account_id"})])
        .drop_duplicates()
        .groupby("account_id")["pattern"].agg(lambda s: "|".join(sorted(s)))
    )
    sends = set(laundering.src)
    receives = set(laundering.dst)
    labels = pd.DataFrame({"n_laundering_tx": n_laundering})
    labels["pattern_types"] = types
    labels["role"] = [
        "pass-through" if a in sends and a in receives else "source" if a in sends else "sink"
        for a in labels.index
    ]
    labels.index.name = "account_id"
    return labels


def subsample(tx, patterns, n_background, max_core_tx, seed):
    """Core = pattern accounts + a label-blind random background sample of accounts.

    The background sample stands in for "the bank's book": feature percentiles are
    ranked against it, so they don't depend on the labels.
    """
    activity = pd.concat([tx.src, tx.dst]).value_counts()
    pattern_accounts = set(patterns.src) | set(patterns.dst)

    # Very busy accounts (bank-wide hubs, up to 170k transfers) would drag in huge
    # numbers of rows; leave them as boundary nodes rather than core accounts.
    core_pattern = {a for a in pattern_accounts if activity.get(a, 0) <= max_core_tx}
    pool = activity[(activity >= 3) & (activity <= max_core_tx)]
    rng = np.random.default_rng(seed)
    background = set(rng.choice(pool.index.to_numpy(), size=n_background, replace=False))

    core = core_pattern | background
    sample = tx[tx.src.isin(core) | tx.dst.isin(core)].copy()
    return sample, core, background


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-background", type=int, default=2000)
    parser.add_argument("--max-core-tx", type=int, default=200)
    args = parser.parse_args()

    trans_path, patterns_path = download()
    print("Loading transactions...")
    tx = load_transactions(trans_path)
    patterns = parse_patterns(patterns_path)
    print(f"Full data: {len(tx):,} transactions, {len(patterns):,} in {patterns.attempt_id.nunique()} laundering patterns")

    rates = usd_rates(tx)
    labels = account_labels(tx, patterns)
    sample, core, background = subsample(tx, patterns, args.n_background, args.max_core_tx, config.SEED)
    sample["amount_usd"] = sample.amount_paid * sample.currency_paid.map(rates)

    config.DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    sample.drop(columns=["from_bank", "from_acct", "to_bank", "to_acct"]).to_parquet(config.TRANSACTIONS, index=False)
    accounts = pd.DataFrame(index=pd.Index(sorted(set(sample.src) | set(sample.dst)), name="account_id"))
    accounts["in_scope"] = accounts.index.isin(core)
    accounts["background"] = accounts.index.isin(background)
    accounts = accounts.join(labels)
    accounts["is_laundering"] = accounts.n_laundering_tx.notna()
    accounts.reset_index().to_parquet(config.LABELS, index=False)

    in_scope = accounts[accounts.in_scope]
    print(f"Subsample: {len(sample):,} transactions, {len(accounts):,} accounts "
          f"({len(in_scope):,} in scope, {in_scope.is_laundering.sum():,} of them laundering)")
    print("USD rates:", rates.round(4).to_dict())


if __name__ == "__main__":
    main()
