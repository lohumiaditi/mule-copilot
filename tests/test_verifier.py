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


def test_times_inside_iso_timestamps_count_as_evidence():
    evidence = {**EVIDENCE, "transfers": [{"ts": "2022-09-09T13:20:00Z"}]}
    assert verifier.check("Paid at 13:20 on 2022-09-09 [SOP Annex III para 4 step 1].", evidence, CLAUSES) == []


AUDIT_EVIDENCE = {   # real fields from account 223-811EDA940, as in the audit's test bench
    "account": {"n_senders": 4, "fan_in_48h": 3, "pass_through_ratio": 0.8149, "cycles_3_5": 8,
                "first_seen": "2022-09-01 02:45:00", "last_seen": "2022-09-10 15:05:00"},
    "recent_transfers": [{"ts": "2022-09-10T14:21:00Z", "amount_usd": 13102.64},
                         {"ts": "2022-09-09T10:16:00Z", "amount_usd": 12928.64}],
}


def _numbers_flagged(sentence):
    return [e for e in verifier.check(f"{sentence} [SOP Annex III para 4 step 1].", AUDIT_EVIDENCE, CLAUSES)
            if e.startswith(("Number", "Date", "Time"))]


def test_parts_of_dates_and_times_no_longer_pass_as_counts():
    assert _numbers_flagged("21 senders") == ["Number 21 is not in the evidence."]       # 14:21
    assert _numbers_flagged("fan-in 16") == ["Number 16 is not in the evidence."]        # 10:16
    assert _numbers_flagged("45 receivers") == ["Number 45 is not in the evidence."]     # 02:45


def test_ratio_as_percentage_needs_a_percent_sign():
    assert _numbers_flagged("81 senders") == ["Number 81 is not in the evidence."]
    assert _numbers_flagged("pass-through 81.49%") == []
    assert _numbers_flagged("pass-through 81.5 percent") == []


def test_dates_and_times_are_matched_whole():
    assert _numbers_flagged("4 senders between 2022-09-01 and 2022-09-10, last at 14:21") == []
    assert _numbers_flagged("first seen 1 Sep 2022, last transfer Sep 10, 2022 at 15:05") == []
    assert _numbers_flagged("seen on 2022-09-11 at 14:43") == [
        "Date 2022-09-11 is not in the evidence.", "Time 14:43 is not in the evidence."]
    assert _numbers_flagged("seen on 11 Sep 2022") == ["Number 11 is not in the evidence."]   # wrong day; the year is fine


def test_other_common_date_and_time_formats():
    evidence = {**AUDIT_EVIDENCE, "context": {"explanation_due": "2026-11-22"}}

    def flagged(sentence):
        return verifier.check(f"{sentence} [SOP Annex III para 4 step 1].", evidence, CLAUSES)

    assert flagged("activity during September 2022") == []
    assert flagged("activity in 2022") == []
    assert flagged("please reply by 22/11/2026") == []
    assert flagged("last transfer at 2:21 pm") == []
    assert flagged("please reply by 23/11/2026") == ["Number 23 is not in the evidence.", "Number 11 is not in the evidence."]
    assert flagged("last transfer at 2:21 am") == ["Time 02:21 is not in the evidence."]


def test_day_shorthand_after_a_date():
    assert _numbers_flagged("transfers on 2022-09-09/10, from 4 senders") == []
    assert _numbers_flagged("transfers on 2022-09-09, 10.") == []
    assert _numbers_flagged("on 2022-09-09, 11.") == ["Date 2022-09-11 is not in the evidence."]
    assert _numbers_flagged("on 2022-09-09, 12 transfers") == ["Number 12 is not in the evidence."]


def test_known_limit_number_beside_the_wrong_label_still_passes():
    assert _numbers_flagged("8 senders") == []   # 8 is the cycle count; labels are not checked


def test_bracketed_field_names_are_not_citations():
    note = "Pass-through 0.8149, minimum 0.8 [pass_through_min] [SOP Annex III para 4 step 1]."
    assert verifier.check(note, {**EVIDENCE, "thresholds": {"pass_through_min": 0.8}}, CLAUSES) == []


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
