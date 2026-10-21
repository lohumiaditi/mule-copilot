"""Neo4j: load the subsampled graph, and the fixed Cypher queries the agent uses.

Only transactions, computed features and the simulated NCRP "already reported" flag
go in. Ground-truth labels stay on disk.

    python -m mule_copilot.graph_store   # (re)load the graph
"""

import pandas as pd
from neo4j import GraphDatabase

from mule_copilot import config

BATCH = 5000


def driver():
    return GraphDatabase.driver(config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))


def _batches(records):
    for i in range(0, len(records), BATCH):
        yield records[i:i + BATCH]


def load(feats, tx, reported_ids=()):
    feats = feats.assign(first_seen=feats.first_seen.astype(str), last_seen=feats.last_seen.astype(str))
    tx = tx.assign(ts=tx.ts.dt.strftime("%Y-%m-%dT%H:%M:%S"))
    accounts = feats.rename(columns={"account_id": "id"}).to_dict("records")
    transfers = tx[["src", "dst", "ts", "amount_paid", "currency_paid", "amount_received",
                    "currency_received", "amount_usd", "payment_format"]].to_dict("records")

    with driver() as d, d.session() as s:
        s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS")
        s.run("CREATE CONSTRAINT account_id IF NOT EXISTS FOR (a:Account) REQUIRE a.id IS UNIQUE")
        for batch in _batches(accounts):
            s.run("UNWIND $rows AS row MERGE (a:Account {id: row.id}) SET a += row", rows=batch)
        s.run("UNWIND $ids AS id MATCH (a:Account {id: id}) SET a.reported_ncrp = true", ids=list(reported_ids))
        for batch in _batches(transfers):
            s.run(
                """
                UNWIND $rows AS row
                MATCH (a:Account {id: row.src}), (b:Account {id: row.dst})
                CREATE (a)-[:TRANSFER {ts: datetime(row.ts), amount_paid: row.amount_paid,
                    currency_paid: row.currency_paid, amount_received: row.amount_received,
                    currency_received: row.currency_received, amount_usd: row.amount_usd,
                    payment_format: row.payment_format}]->(b)
                """,
                rows=batch,
            )
        counts = s.run("MATCH (a:Account) WITH count(a) AS n MATCH ()-[t:TRANSFER]->() RETURN n, count(t) AS m").single()
    return counts["n"], counts["m"]


# --- Fixed queries used by the agent's evidence step -------------------------------

def account(account_id):
    with driver() as d:
        records, _, _ = d.execute_query("MATCH (a:Account {id: $id}) RETURN properties(a) AS p", id=account_id)
    return records[0]["p"] if records else None


def transfers(account_id, limit=50):
    """Most recent transfers in and out of the account, excluding self-transfers."""
    query = """
        MATCH (a:Account {id: $id})-[t:TRANSFER]-(other:Account)
        WHERE other <> a
        RETURN CASE WHEN startNode(t) = a THEN 'out' ELSE 'in' END AS direction,
               other.id AS counterparty, toString(t.ts) AS ts, t.amount_paid AS amount,
               t.currency_paid AS currency, round(t.amount_usd, 2) AS amount_usd, t.payment_format AS format
        ORDER BY t.ts DESC LIMIT $limit
    """
    with driver() as d:
        records, _, _ = d.execute_query(query, id=account_id, limit=limit)
    return [r.data() for r in records]


def community_peers(account_id, limit=25):
    """Other in-scope accounts in the same Louvain community, busiest first."""
    query = """
        MATCH (a:Account {id: $id}), (p:Account {community_id: a.community_id})
        WHERE p <> a AND p.in_scope
        RETURN p.id AS id, p.fan_in_48h AS fan_in_48h, p.fan_out_48h AS fan_out_48h,
               p.pass_through_ratio AS pass_through_ratio, p.cycles_3_5 AS cycles_3_5
        ORDER BY p.n_tx_in + p.n_tx_out DESC LIMIT $limit
    """
    with driver() as d:
        records, _, _ = d.execute_query(query, id=account_id, limit=limit)
    return [r.data() for r in records]


def cycle_paths(account_id, limit=5):
    """Example directed cycles of length 3 to 5 through the account."""
    query = """
        MATCH path = (a:Account {id: $id})-[:TRANSFER*3..5]->(a)
        WHERE all(n IN nodes(path)[1..-1] WHERE n <> a)
        RETURN DISTINCT [n IN nodes(path) | n.id] AS accounts LIMIT $limit
    """
    with driver() as d:
        records, _, _ = d.execute_query(query, id=account_id, limit=limit)
    return [r["accounts"] for r in records]


def reported_links(account_id, limit=10):
    """Counterparties already on the (simulated) NCRP reported list: SOP indicator (b)."""
    query = """
        MATCH (a:Account {id: $id})-[t:TRANSFER]-(r:Account {reported_ncrp: true})
        WHERE r <> a
        RETURN r.id AS id, count(t) AS transfers, round(sum(t.amount_usd), 2) AS amount_usd
        ORDER BY transfers DESC LIMIT $limit
    """
    with driver() as d:
        records, _, _ = d.execute_query(query, id=account_id, limit=limit)
    return [r.data() for r in records]


def main():
    feats = pd.read_parquet(config.FEATURES)
    tx = pd.read_parquet(config.TRANSACTIONS)
    reported = pd.read_csv(config.NCRP_REPORTED).account_id if config.NCRP_REPORTED.exists() else []
    n, m = load(feats, tx[tx.src != tx.dst], reported)
    print(f"Loaded {n:,} Account nodes and {m:,} TRANSFER edges into {config.NEO4J_URI} "
          f"({len(reported):,} flagged as already reported)")


if __name__ == "__main__":
    main()
