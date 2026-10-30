from mule_copilot import evals


def test_metrics_counts_hits_misses_and_false_alarms():
    truth = [True, True, True, False, False]
    predicted = [True, True, False, True, False]
    m = evals._metrics(truth, predicted)
    assert (m["tp"], m["fp"], m["fn"]) == (2, 1, 1)
    assert round(m["precision"], 3) == round(2 / 3, 3)
    assert round(m["recall"], 3) == round(2 / 3, 3)


def test_metrics_with_no_positive_predictions():
    assert evals._metrics([True, False], [False, False])["precision"] == 0.0


def test_stated_hold_reads_the_first_hold_named():
    def section(text):
        return f"## Patterns matched\nan account\n## Recommended hold level\n{text}\n## Confidence\nhigh"

    assert evals.stated_hold(section("**Account** level hold (no hold on amounts alone)")) == "account"
    assert evals.stated_hold(section("Transaction-level hold on the flagged amounts")) == "transaction"
    assert evals.stated_hold(section("None. No temporary debit hold is recommended.")) == "none"
    assert evals.stated_hold(section("To be decided")) == "unclear"
