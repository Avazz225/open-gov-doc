"""Pairing detached signatures in a message (P92-S4). Pure, no DB."""

from mail_connector.signature_pairing import pair_detached_signatures


def test_name_dot_ext_pairs_with_name_dot_ext_p7s():
    names = ["Nachricht.txt", "bescheid.pdf", "anlage.docx", "Bescheid.PDF.p7s", "anlage.docx.p7s"]
    assert pair_detached_signatures(names, skip={0}) == {3: 1, 4: 2}


def test_one_file_and_one_signature_pair_up():
    assert pair_detached_signatures(["body.txt", "scan.pdf", "signatur.p7s"], skip={0}) == {2: 1}


def test_ambiguous_signatures_stay_unpaired():
    names = ["body.txt", "a.pdf", "b.pdf", "sig.p7s"]
    assert pair_detached_signatures(names, skip={0}) == {}
    assert pair_detached_signatures(["body.txt", "a.pdf", "x.p7s", "y.p7s"], skip={0}) == {}


def test_the_body_never_takes_part():
    assert pair_detached_signatures(["body.txt", "body.txt.p7s"], skip={0}) == {}


def test_name_rule_first_then_the_single_rest():
    names = ["body.txt", "a.pdf", "a.pdf.p7s", "b.xml", "unterschrift.p7s"]
    assert pair_detached_signatures(names, skip={0}) == {2: 1, 4: 3}
