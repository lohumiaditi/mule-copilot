# Demo script (2 minutes)

Two cases, both sides of the SOP: a suspected mule held and referred, an honest customer cleared fast.

## Before recording

1. `powershell -ExecutionPolicy Bypass -File scripts\neo4j.ps1 start`
2. `.venv\Scripts\streamlit run src\mule_copilot\app.py`
3. Set "Today (simulated)" in the sidebar to 2 Nov 2026.
4. Each LLM step takes 5 to 30 seconds. Either cut the waits in editing, or open both cases once
   before recording ("Run copilot") so the case notes are ready.

## Shot list

| Time | Screen | Say |
|---|---|---|
| 0:00-0:15 | README top: the three numbers | "About ₹19,800 crore lost to cyber fraud in 2025, over 27 lakh first-layer mule accounts identified, and RBI's draft SOP for suspected mule accounts proposed to apply from 1 April 2027. Every flag needs a cited case note, a hold, a notice and a clock. This copilot does the drafting; the officer decides." |
| 0:15-0:35 | Run copilot on `223-811EDA940`; case note tab | "Evidence comes from Neo4j with fixed queries. Rules, not the LLM, find pass-through and round-tripping, so the recommendation is an account-level hold. Every number and clause in the note has been checked in code." Point at a `[SOP Annex III …]` citation and the "Verifier passed" banner. |
| 0:35-0:45 | Graph tab | "Here are the counterparties, with the money cycling back through three accounts." |
| 0:45-1:05 | Approve; explanation panel: type "I sold my old phone online…"; Record | "The customer gets the notice the SOP requires, with 20 days to explain. This explanation doesn't account for 19 credits from 4 senders and 8 cycles, so the copilot says continue." |
| 1:05-1:15 | Continue; approve police reference + STR; Documents tab | "Police reference through NCRP and the STR are drafted for approval. The customer's letter never mentions the STR; that is checked too." |
| 1:15-1:40 | Run copilot on `11405-8014EFB60`; Approve; Load sample explanation; Record | "Now an exporter whose money moves fast. Their explanation of two distributors and four suppliers matches the 2 senders and 4 receivers in the data, so the copilot recommends removing the hold." Click Remove hold. |
| 1:40-1:50 | Case queue and MIS log tab | "Cleared in days rather than waiting out 30. Every step, date and actor is in the MIS log the SOP asks for, and the queue is sorted by the next SOP deadline." |
| 1:50-2:00 | README eval table | "Measured on 30 accounts and 15 explanations. Results are honest, including the weak ones. Built against the draft SOP on synthetic data." |
