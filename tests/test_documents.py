from mule_copilot import documents, verifier


def test_tipping_off_guard_catches_reporting_terms_only():
    for text in ["We have filed an STR.", "reported to FIU-IND", "a Suspicious Transaction Report", "money-laundering"]:
        assert documents.TIPPING_OFF.search(text), text
    for text in ["a strong pattern", "the string of transfers", "your account"]:
        assert not documents.TIPPING_OFF.search(text), text


def test_hold_level_check():
    wrong = "The bank shall place a Temporary Debit Hold on the entire account."
    assert verifier.check_hold_level(wrong, "transaction")
    assert not verifier.check_hold_level(wrong, "account")
    quote = "a hold on the Suspected Money Mule Transaction or, if the account is suspected, on the entire account"
    assert not verifier.check_hold_level(quote, "transaction")
