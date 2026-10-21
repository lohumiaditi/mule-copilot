from mule_copilot import corpus


def _page(paragraphs):
    body = "".join(paragraphs)
    return f'<html><body><table class="td"><tr><td>{body}</td></tr></table></body></html>'


DRAFT = _page([
    "<p>Reserve Bank has issued instructions on money mules.</p>",
    "<p>3. Short Title and Commencement</p><p>(1) These Directions shall be called X.</p>",
    "<p>4. These Amendment Directions shall modify the said Directions:</p>",
    "<p>(1) Applicable paragraph shall be substituted by the following, namely:</p>",
    "<p>The bank shall monitor transactions to identify Money Mules.</p>",
    "<p>(2) In Annexures, the following Annex shall be inserted:</p>",
    "<p>“Annex III - Standard Operating Procedure on Suspected Money Mule Accounts</p>",
    "<p>1. Background</p><p>This SOP lays down a procedure.</p>",
    "<p>3. Definitions</p><p>“Days” means calendar days.</p>",
    "<p>5. Procedure and Timeline for Removal of Temporary Debit Hold</p>",
    "<table><tr><td>Step</td><td>Action</td><td>Timeline</td></tr>",
    "<tr><td>3</td><td>Seek the explanation.</td><td>20 days from the hold.</td></tr></table>",
    "<p>Maximum duration is 60 days.</p>",
    "<p>6. Internal Policy</p><p>(ii) account-level hold only as a last resort;</p>",
    "<p>”.</p><p>(Signature)</p>",
])


def test_split_draft_gives_stable_clause_ids():
    clauses = {c["id"]: c for c in corpus.split_draft(corpus.html_lines(DRAFT))}
    assert clauses["Draft Amendment 2026 para 4(1)"]["text"] == "The bank shall monitor transactions to identify Money Mules."
    assert clauses["SOP Annex III para 5 step 3"]["text"] == "Seek the explanation. Timeline: 20 days from the hold."
    assert clauses["SOP Annex III para 3 (Days)"]["text"] == "“Days” means calendar days."
    assert "60 days" in clauses["SOP Annex III para 5 (maximum duration)"]["text"]
    assert clauses["SOP Annex III para 6(ii)"]["status"] == "draft"
    assert clauses["SOP Annex III para 1"]["text"] == "This SOP lays down a procedure."
    assert not any("Signature" in c["text"] for c in clauses.values())


def test_split_kyc_keeps_selected_paragraphs_in_order():
    paras = [f"<p>{n}. Filler paragraph {n}.</p>" for n in range(1, 68)]
    paras[4] = ("<p>5. In these Directions:</p><p>(xviii) ‘Suspicious transaction’ means a transaction which:</p>"
                "<p>(a) gives rise to suspicion;</p><p>(xix) A ‘Small Account’ means ...</p>")
    paras += ["<p>Chapter X – Other Instructions</p>",
              "<p>68. Operation of Bank Accounts and Money Mules: The bank shall adhere.</p>",
              "<p>3. A stray numbered line that is not paragraph 69.</p>"]
    clauses = {c["id"]: c for c in corpus.split_kyc(corpus.html_lines(_page(paras)))}
    assert set(clauses) == {f"KYC-CB-2025 para {n}" for n in corpus.KYC_PARAS} | {"KYC-CB-2025 para 5(1)(xviii)"}
    assert clauses["KYC-CB-2025 para 68"]["text"].endswith("The bank shall adhere. 3. A stray numbered line that is not paragraph 69.")
    assert clauses["KYC-CB-2025 para 5(1)(xviii)"]["text"] == (
        "(xviii) ‘Suspicious transaction’ means a transaction which: (a) gives rise to suspicion;")
