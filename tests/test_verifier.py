from mule_copilot import verifier

CLAUSES = {"SOP Annex III para 4 step 1", "SOP Annex III para 5 step 3"}
EVIDENCE = {
    "account": {"id": "223-811EDA940", "pass_through_ratio": 0.8149, "total_in_usd": 198124.41, "fan_in_48h": 3},
    "clauses": [{"id": "SOP Annex III para 5 step 3", "text": "The bank shall give 20 days' time ..."}],
}


def test_clean_note_passes():
    note = ("Account 223-811EDA940 received USD 198,124.41; 81.49% was sent on, ratio 0.8149. "
            "Fan-in over 48 hours was 3. Hold now [SOP Annex III para 4 step 1] and allow 20 days "
            "for an explanation [SOP Annex III para 5 step 3].")
    assert verifier.check(note, EVIDENCE, CLAUSES) == []


def test_account_ids_with_unicode_hyphens_are_not_numbers():
    note = "Sent to 11107‑80117C890 [SOP Annex III para 4 step 1]."
    assert verifier.check(note, EVIDENCE, CLAUSES) == []


def test_invented_number_is_caught():
    note = "Received USD 250,000.00 [SOP Annex III para 4 step 1]."
    assert verifier.check(note, EVIDENCE, CLAUSES) == ["Number 250000.00 is not in the evidence."]


def test_unknown_and_missing_citations_are_caught():
    assert "No clause is cited." in verifier.check("Ratio 0.8149.", EVIDENCE, CLAUSES)
    errors = verifier.check("Ratio 0.8149 [SOP Annex III para 9].", EVIDENCE, CLAUSES)
    assert errors == ["Cited clause [SOP Annex III para 9] does not exist in the corpus."]


def test_citation_formatting_is_forgiven_but_not_content():
    note = "Hold [**SOP Annex III para 4 step 1**] and [SOP Annex III para 5 step 3]."
    assert verifier.check(note, EVIDENCE, CLAUSES) == []
    assert "No clause is cited." in verifier.check("Hold **SOP Annex III para 4 step 1**.", EVIDENCE, CLAUSES)
    assert verifier.check("Hold [​SOP Annex III para 4 step 1] 【SOP Annex III para 5 step 3】.", EVIDENCE, CLAUSES) == []


def test_rounding_to_the_notes_own_precision_is_accepted():
    note = "Pass-through 81.5%, about USD 198,124 in [SOP Annex III para 4 step 1; SOP Annex III para 5 step 3]."
    assert verifier.check(note, EVIDENCE, CLAUSES) == []
