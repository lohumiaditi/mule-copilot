# Mule Copilot

An agentic copilot for mule-account investigation, built against RBI's September 2026 draft SOP
(draft KYC Amendment Directions 2026, Annex III). Flagged account in, cited case note out, human
approval, then customer notice, police (NCRP) reference or STR draft, all logged.

> Built against a **draft**. The final directions may change; this is not a compliance claim.
> Synthetic data only (IBM's public AML dataset). No bank data, code or process details are used.

## Status

- [x] Day 1: synthetic transaction graph in Neo4j with mule-pattern features, frozen eval set
- [ ] Day 2: RAG over the SOP and guidance, LangGraph case-note agent with verifier
- [ ] Day 3: SOP clock, explanation assessor, drafted documents, MIS log, Streamlit
- [ ] Day 4: evals, production design, demo

## Setup

Python 3.11+ and either Docker or the portable Neo4j script (Windows, no admin rights needed).

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
copy .env.example .env          # then add your GROQ_API_KEY

# Neo4j: either
docker compose up -d
# or, without Docker (downloads Temurin JDK 21 + Neo4j Community into .tools\)
powershell -ExecutionPolicy Bypass -File scripts\neo4j.ps1 setup
powershell -ExecutionPolicy Bypass -File scripts\neo4j.ps1 start
```

## Build the graph

```powershell
.venv\Scripts\python -m mule_copilot.ingest       # download HI-Small from Kaggle, subsample
.venv\Scripts\python -m mule_copilot.features     # fan-in/out, pass-through, cycles, Louvain
.venv\Scripts\python -m mule_copilot.graph_store  # load into Neo4j
.venv\Scripts\python -m mule_copilot.eval_set     # freeze the 30-account eval set
.venv\Scripts\python -m mule_copilot.profile <account_id>
```

## Data

IBM [Transactions for Anti Money Laundering](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml),
`HI-Small` file: 5 million synthetic transactions, 370 labelled laundering patterns (fan-in, fan-out,
cycle, scatter-gather, gather-scatter, stack, bipartite, random).

The subsample has two kinds of "in-scope" account, and keeps every transaction of each:

- every account in a labelled pattern with at most 200 transfers (3,138 of 3,170; the 32 busier
  ones stay as boundary nodes), and
- a label-blind random **background** sample of 2,000 accounts, standing in for the bank's book.
  About 1.6% of it turns out to be laundering, as you'd expect from a random draw.

Result: 150,642 transactions (140,865 after dropping self-transfers), 20,393 accounts, 5,124 in
scope. Counterparties outside the core are loaded as boundary nodes with partial features.
Ground-truth labels are kept in a separate file and never loaded into Neo4j, so the copilot cannot
read them.

## Eval set

[`evals/eval_accounts.csv`](evals/eval_accounts.csv), frozen with seed 42:

- **20 laundering**: in-scope pattern members with at least 2 laundering transactions (accounts
  touched by a single laundering transfer look like ordinary counterparties, not mules), sampled
  round-robin across the 8 pattern types. 16 pass-through, 2 sink, 2 source.
- **10 clean**: in-scope, no laundering transactions. 5 from the busiest decile (hard negatives),
  5 random.

## Features

| Feature | Definition | Mule signal |
|---|---|---|
| `fan_in_48h` / `fan_out_48h` | Most distinct senders / receivers in any 48-hour window | Many small credits collected, or quickly dispersed |
| `pass_through_ratio` | min(in, out) / max(in, out), in USD | Money in goes straight out |
| `cycles_3_5` | Directed simple cycles of length 3 to 5 through the account | Round-tripping |
| `community_id` / `community_size` | Louvain community on the transfer graph | Ring membership |

Each feature also has a `_pctl` value: the share of background accounts with a strictly lower
value (so 0 cycles ranks 0, and any cycle ranks above every background account). Features are
computed in Python (pandas, NetworkX) and written to Neo4j, so the Graph Data Science plugin is
optional.

Excerpt of `python -m mule_copilot.profile 223-811EDA940` (a cycle and fan-in pattern member):

```
Feature                                  Value  Percentile
Fan-in, 48h (distinct senders)               3       82.7%
Fan-out, 48h (distinct receivers)            2       70.3%
Pass-through ratio                      0.8149       97.6%
Cycles of length 3-5                         8      100.0%
Cycle: 223-811EDA940 -> 48308-811ED7DF0 -> 119-81235E8B0 -> 148389-811EDAA30 -> 223-811EDA940
```

## Limitations

- **Community size is a sampling artefact here.** Whole laundering patterns are kept while the
  background accounts' neighbours are truncated, so pattern accounts sit in communities of about
  60 and clean ones in communities of about 4. It is stored for the graph view but is not used for
  scoring, because it would inflate detection numbers.
- Cycles are structural (any order of timestamps), not time-respecting.
- Amounts are converted to USD with exchange rates implied by the dataset's own cross-currency
  transfers.
