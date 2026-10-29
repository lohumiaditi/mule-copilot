# Mule Copilot in a bank: production design (one page)

**What it would do.** Take every account the bank's monitoring flags as a suspected money mule,
assemble the evidence, recommend a hold level under RBI's SOP, draft the case note, customer notice,
police reference and STR, keep the SOP clock, and log every step. An officer approves every action.
The prototype is built against RBI's September 2026 draft; the final directions may differ.

## Integration points

| Direction | System | What flows |
|---|---|---|
| In | Transaction monitoring / FRM (incl. MuleHunter.AI scores if used) | Flag events: account, transactions, alert reason |
| In | Core banking + KYC | Declared occupation and income, account age, balances: replaces the prototype's "bank's book" proxy for SOP indicator (a) |
| In | NCRP-CFCFRMS and I4C Suspect Registry | Accounts already reported: SOP indicator (b), replacing the simulated list |
| In | Payments data (UPI, IMPS, NEFT, RTGS) | Transfers into the graph store, rolling 90 days, near real time for flagged accounts |
| Out | Core banking hold API | Amount-level lien by default; account-level debit freeze only on officer approval (SOP para 6(ii)) |
| Out | Customer communications gateway | Notices by SMS / email / letter from compliance-approved templates, same day (step 2) |
| Out | NCRP-CFCFRMS | Police reference with reasons; record the date of reference (step 4b) |
| Out | Existing AML / FIU-IND reporting (FINnet) | STR grounds of suspicion as a draft into the AML team's queue, never sent directly |
| Both | Case management and MIS | Cases, deadlines, documents, decisions; retained 5 years from hold, 10 after closure (para 7) |

## Hosting and data

- **LLM on premises or India-hosted.** The prototype uses `gpt-oss-120b` on Groq with synthetic data
  only. In production the same open-weight model runs on the bank's own GPUs (for example with vLLM)
  or in an India-region private deployment. No customer data leaves the bank's boundary; embeddings
  already run locally.
- **Minimise what the model sees.** Prompts carry derived features and transfer summaries, not names,
  addresses or full KYC records. Account numbers are masked in prompts and mapped back in code.
- **DPDP Act 2023 and Rules 2025** (notified 13 November 2025; most obligations apply from 13 May
  2027, close to the SOP's proposed start). Processing rests on legal obligations (PMLA, RBI
  directions), with purpose limitation, retention matched to the SOP's record rules, and access
  logging. Legal to confirm the lawful basis for each data flow.

## Model risk and governance

- **The rules are the model.** Typology thresholds go through model risk validation: back-tested on
  confirmed mule cases and NCRP outcomes, with documented false-hold rates by segment. Threshold
  changes are versioned and approved.
- **The LLM is a drafting tool, checked in code.** Every number must come from the evidence, every
  citation must exist, holds must match the rules, customer letters must not reveal an STR. Failures
  go back once, then to the officer, flagged.
- **A human decides every action**: note, hold, decision, referral, filing. Every prompt, model
  version, draft and decision is logged for audit. This fits RBI's FREE-AI framework (August 2025),
  which calls for board-approved AI policies, AI-specific audit mechanisms and clear accountability.

## Rollout

1. **Shadow mode, 8 to 12 weeks.** The copilot drafts alongside investigators on live flags; nothing
   it produces is acted on. Compare its hold level and assessments with officers' decisions and
   with later NCRP confirmations.
2. **Assisted mode, one region.** Officers start from the copilot's drafts and approve every step.
   Weekly review of overrides, false holds and verifier flags.
3. **Scale**, with a kill switch back to the manual process and quarterly revalidation.

## Business-case KPIs

| KPI | Why it matters |
|---|---|
| Investigator minutes per case (baseline about 90 for a manual write-up) | Capacity: the bottleneck as flags grow |
| Share of decisions inside the SOP's 10 / 30-day windows; holds past 60 days (target 0) | Regulatory compliance |
| Honest customers held, and days to clear them | Customer harm and complaints |
| Override rate of the copilot's hold level and assessments | Model quality, drift |
| Police references with an instruction; STRs accepted without query | Downstream usefulness |
| Grievances raised, and share resolved within 30 days (para 8) | Customer outcomes |

**Capacity illustration (estimate).** 1,000 flagged accounts at about 90 minutes each is about
1,500 investigator-hours. If officer review of a verified draft takes about 15 minutes, that is
about 250 hours. These are assumptions to be replaced by shadow-mode measurements. Measured on the
prototype: median 30 seconds and about 5,400 LLM tokens from account ID to verified case note.

<sub>Sources: [RBI draft KYC Amendment Directions 2026](https://www.rbi.org.in/scripts/bs_viewcontent.aspx?Id=5170) ·
[RBI FREE-AI framework, summary (KPMG)](https://kpmg.com/in/en/insights/2025/08/rbi-free-ai-committee-report-on-framework-for-responsible-and-ethical-enablement-of-artificial-intelligence.html) ·
[DPDP Rules 2025 (SCC Online)](https://www.scconline.com/blog/post/2025/11/14/meity-notified-digital-personal-data-protection-rules-2025/) ·
[MuleHunter.AI in banks (TaxGuru)](https://taxguru.in/rbi/ai-tool-launched-to-detect-mule-accounts-used-in-cyber-fraud.html)</sub>
