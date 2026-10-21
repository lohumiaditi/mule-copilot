# Mule Copilot

An agentic copilot for mule-account investigation, built against RBI's September 2026 draft SOP
(draft KYC Amendment Directions 2026, Annex III). Flagged account in, cited case note out, human
approval, then customer notice, police (NCRP) reference or STR draft, all logged.

> Built against a **draft**. The final directions may change; this is not a compliance claim.
> Synthetic data only (IBM's public AML dataset). No bank data, code or process details are used.

## Status

- [x] Day 1: synthetic transaction graph in Neo4j with mule-pattern features, frozen eval set
- [x] Day 2: clause-level RAG over the RBI texts, typology rules, LangGraph case-note agent with
      verifier and officer review
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
.venv\Scripts\python -m mule_copilot.features     # fan-in/out, pass-through, rapid forwarding, cycles, Louvain
.venv\Scripts\python -m mule_copilot.eval_set     # frozen eval set + simulated NCRP "reported" list
.venv\Scripts\python -m mule_copilot.graph_store  # load into Neo4j
.venv\Scripts\python -m mule_copilot.profile <account_id>
```

## Run the copilot

```powershell
.venv\Scripts\python -m mule_copilot.corpus                      # fetch RBI texts, split into clauses, embed
.venv\Scripts\python -m mule_copilot.corpus "time to submit explanation"   # try retrieval
.venv\Scripts\python -m mule_copilot.agent 223-811EDA940         # case note, then officer approve/edit/reject
.venv\Scripts\python -m mule_copilot.agent 223-811EDA940 --auto-approve --no-linkage
```

Notes land in `data/processed/notes/<account>.md`, with a `.json` beside each holding the evidence,
cited clauses, every draft and its verifier errors, the officer's decision and the time taken.

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
| `rapid_forward_24h` | Share of inbound USD sent out again within 24 hours, matching outflows to the oldest inflows first | Money doesn't stay: the classic mule signature |
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

## Sources

Fetched as HTML from rbi.org.in and split into 42 clauses, each with a stable ID that notes cite:

| Source | Status | Clauses |
|---|---|---|
| [Draft RBI (KYC) Amendment Directions, 2026](https://www.rbi.org.in/scripts/bs_viewcontent.aspx?Id=5170) (11 Sep 2026), incl. Annex III SOP on suspected money mule accounts | Draft; comments closed 2 Oct 2026; proposed start 1 Apr 2027 | Every SOP paragraph, each step 1 to 6, each definition, each internal-policy item, e.g. `SOP Annex III para 5 step 3` |
| [RBI (Commercial Banks – KYC) Directions, 2025](https://www.rbi.org.in/scripts/BS_ViewMasDirections.aspx?id=13141) (updated 1 Oct 2026) | In force | Para 68 (money mules), 39 to 41 (monitoring), 50 (records), 52 to 55 (FIU-IND reporting), 5(1)(xviii) (suspicious transaction) |

Also kept for reference: the [consultation notice](https://rbidocs.rbi.org.in/rdocs/PublicConsultation/PDFs/C2R50511092026B2AFC9BDCF294823A302C9E2EF58667B.PDF)
and [press release](https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=63587).

FIU-IND's guidance on mule accounts was planned as a third source for red-flag indicators. No public
copy could be found (it appears to go to reporting entities only), so it is not used. The SOP's own
definition of a suspected money mule transaction supplies the indicators instead.

Clauses are embedded with `BAAI/bge-small-en-v1.5` into a local Chroma store. Small embedding models
are imperfect on legal text, so each typology signal also pins its "anchor" clauses, and retrieval
only adds context on top.

## How a mule is identified

The SOP (Annex III para 3) defines a suspected money mule transaction as one of ₹1,000 or more,
flagged by monitoring, on indicators such as **(a)** being unusual for or disproportionate to the
account holder's declared profile, or **(b)** linkage to an account already reported as a money mule.

**Rules decide; the LLM only writes.** Thresholds come from the background sample, never the labels:

| Signal | Rule | SOP indicator |
|---|---|---|
| Collection | `fan_in_48h` in the top 5% of the book | (a) |
| Dispersal | `fan_out_48h` in the top 5% | (a) |
| Pass-through | `pass_through_ratio` ≥ 0.8, money both in and out | (a) |
| Rapid forwarding | `rapid_forward_24h` in the top 5% | (a) |
| Round-tripping | at least one 3 to 5 cycle | (a) |
| Linkage | transfers with an account on the NCRP "already reported" list | (b) |

IBM's data has no declared customer profile, so for (a) the bank's book stands in for it.

**Hold level** follows the SOP: no signal, no hold; one signal, a hold on the flagged amounts and a
request for an explanation (Annex III para 4 step 1, para 5 step 3); two or more, an account-level
hold, which the SOP allows only as a last resort (para 6(ii)).

**The NCRP list is simulated.** A random 30% of pattern accounts, never including an eval account,
stand in for accounts already reported by fraud victims. This uses labels on purpose, because in
practice that is how first-layer mules are found, so every result is reported with and without it.

On held-out accounts (eval set and reported accounts excluded):

| | Mule-like laundering (762) | Clean (1,958) |
|---|---|---|
| Collection | 10.6% | 2.6% |
| Dispersal | 17.5% | 4.8% |
| Pass-through | 23.8% | 2.4% |
| Rapid forwarding | 18.2% | 4.9% |
| Round-tripping | 8.3% | 0.0% |
| Linkage | 58.1% | 1.1% |
| **Any hold**, without linkage | 58.3% | 13.1% |
| **Any hold**, with linkage | 78.6% | 13.6% |
| **Account-level hold**, without linkage | 18.1% | 1.6% |
| **Account-level hold**, with linkage | 43.8% | 2.0% |

Transaction-level holds are the triage tier: the customer's explanation (Day 3) is what clears
honest customers quickly. Account-level holds are rare on clean accounts.

## Case-note agent

```
intake → evidence → typology → retrieve → draft → verify → officer review → end
                                            ↑______| (once, with the errors)
```

- **Evidence**: fixed Cypher queries only (account facts, last 15 transfers, example cycles,
  reported counterparties). No free-form queries.
- **Draft**: Groq `openai/gpt-oss-120b`, temperature 0, told to copy numbers rather than compute them
  and to cite only the clauses it was given. Sections: account summary, evidence, patterns matched,
  red flags and SOP basis, recommended hold level, confidence.
- **Verifier** (plain code): every number in the note must appear in the evidence or the cited
  clauses (rounding and ratio-as-percentage allowed); every `[clause ID]` must exist; at least one
  must be cited. A failing draft goes back once with the errors, then is flagged to the officer.
  Example catch: a first draft summed several transfers into "≈ USD 115,000", a number in no record.
- **Officer review**: LangGraph `interrupt()`; the officer approves, edits or rejects, and the
  decision is stored with the case.

## Limitations

- **Community size is a sampling artefact here.** Whole laundering patterns are kept while the
  background accounts' neighbours are truncated, so pattern accounts sit in communities of about
  60 and clean ones in communities of about 4. It is stored for the graph view but is not used for
  scoring, because it would inflate detection numbers.
- Cycles are structural (any order of timestamps), not time-respecting.
- Amounts are converted to USD with exchange rates implied by the dataset's own cross-currency
  transfers.
- No declared customer profile exists in the data, so SOP indicator (a) is proxied by the bank's
  book. A real deployment would compare against KYC-declared income and occupation.
- The linkage indicator relies on a simulated NCRP list built from labels (see above).
- The SOP's ₹1,000 floor is not applied: IBM amounts are in mixed currencies, not rupees.
