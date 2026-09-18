"""CCF venue rank mapping tests."""

from radar.venue_ranks import ccf_rank_for_venue


def test_known_ccf_venues_rank():
    assert ccf_rank_for_venue("NeurIPS") == "A"
    assert ccf_rank_for_venue("ICML") == "A"
    assert ccf_rank_for_venue("ACL") == "A"
    assert ccf_rank_for_venue("EMNLP") == "B"
    assert ccf_rank_for_venue("ICPR") == "C"


def test_unknown_venue_returns_none():
    assert ccf_rank_for_venue("arXiv") is None
    assert ccf_rank_for_venue(None) is None
    assert ccf_rank_for_venue("Some Random Workshop") is None


def test_full_journal_names_rank():
    assert ccf_rank_for_venue("IEEE Transactions on Pattern Analysis and Machine Intelligence") == "A"
    assert ccf_rank_for_venue("Journal of Machine Learning Research") == "A"
    assert ccf_rank_for_venue("IEEE Transactions on Knowledge and Data Engineering") == "A"
