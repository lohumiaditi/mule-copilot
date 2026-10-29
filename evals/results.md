# Eval results

Frozen sets: 30 accounts (20 laundering, 10 clean), 15 customer explanations. Case date 2026-11-02; model `openai/gpt-oss-120b`.

| Metric | Test set | Result |
|---|---|---|
| Detection: any hold (precision / recall) | 30 accounts | with linkage 74% / 70% (14 of 20 caught, 5 of 10 clean held); without 71% / 60% (12 caught, 5 clean held) |
| Detection: account-level hold (precision / recall) | 30 accounts | with linkage 92% / 55%; without 80% / 20% |
| Citation accuracy | 15 notes, 80 citations | 80 of 80 exist; of 80 hand-checked, 69 support the claim, 8 partly, 3 do not |
| Verifier catch rate | 30 notes | As run: 4 of 8 drafts sent back had a real error; 1 note(s) still flagged after the retry. After fixing the two verifier bugs those false alarms exposed, the same drafts give 4 of 4 real, 0 flagged (fixes informed by this set) |
| Explanation-assessor agreement | 15 explanations | 12 of 15 (80%); 0 would have released a mule account, 3 kept an honest customer on hold |
| Time per case | 30 accounts | mean 34s, median 30s, max 90s from account ID to verified note, vs 90 min assumed manual; 5,377 LLM tokens per note on average (22 notes measured) |

Citation and verifier judgments: first pass by an AI coding assistant, recorded with reasons in [citation_review.csv](citation_review.csv) and [verifier_review.csv](verifier_review.csv) for review.

The hold level each note states matched the rules in 30 of 30 cases; 22 of 30 notes passed the verifier on the first draft.

```json
{
 "detection_with_linkage": {
  "tp": 14,
  "fp": 5,
  "fn": 6,
  "precision": 0.7368421052631579,
  "recall": 0.7,
  "f1": 0.717948717948718
 },
 "account_hold_with_linkage": {
  "tp": 11,
  "fp": 1,
  "fn": 9,
  "precision": 0.9166666666666666,
  "recall": 0.55,
  "f1": 0.6874999999999999
 },
 "detection_without_linkage": {
  "tp": 12,
  "fp": 5,
  "fn": 8,
  "precision": 0.7058823529411765,
  "recall": 0.6,
  "f1": 0.6486486486486486
 },
 "account_hold_without_linkage": {
  "tp": 4,
  "fp": 1,
  "fn": 16,
  "precision": 0.8,
  "recall": 0.2,
  "f1": 0.32000000000000006
 },
 "notes": {
  "n": 30,
  "first_draft_pass": 22,
  "drafts_sent_back": 8,
  "flagged_after_retry": 1,
  "hold_matches_rules": 30,
  "seconds_mean": 34.14666666666667,
  "seconds_median": 29.9,
  "seconds_max": 89.6,
  "tokens_per_note_mean": 5377.136363636364,
  "notes_with_token_counts": 22
 },
 "citations": {
  "n": 80,
  "notes": 15,
  "exist": 80,
  "judged": 80,
  "supports_yes": 69,
  "supports_partly": 8,
  "supports_no": 3
 },
 "verifier": {
  "drafts_sent_back": 8,
  "with_real_error": 4,
  "errors_flagged": 9,
  "errors_real": 4
 },
 "verifier_after_fix": {
  "drafts_sent_back": 4,
  "with_real_error": 4,
  "flagged": 0
 },
 "assessor": {
  "n": 15,
  "agree": 12,
  "by_difficulty": {
   "easy": {
    "sum": 4,
    "count": 5
   },
   "medium": {
    "sum": 4,
    "count": 6
   },
   "tricky": {
    "sum": 4,
    "count": 4
   }
  },
  "wrongly_removed": 0,
  "wrongly_continued": 3
 }
}
```
