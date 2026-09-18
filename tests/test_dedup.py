"""Cross-source deduplication tests."""

from radar.schemas import SourceRecord
from radar.services.dedup import SourceDeduplicator


def _record(external_id, *, doi=None, arxiv_id=None, title="Same Paper", abstract="a"):
    return SourceRecord(
        external_id=external_id,
        title=title,
        authors=[],
        abstract=abstract,
        url="http://example.com",
        doi=doi,
        arxiv_id=arxiv_id,
    )


def test_same_doi_different_external_ids_dedupes():
    dedup = SourceDeduplicator()
    dedup.add(_record("openalex:W1", doi="10.1000/xyz", abstract="short"))
    dedup.add(_record("pubmed:123", doi="10.1000/xyz", abstract="a much longer abstract"))
    values = dedup.values()
    assert len(values) == 1
    # The richer abstract wins.
    assert values[0].abstract == "a much longer abstract"


def test_same_arxiv_id_different_external_ids_dedupes():
    dedup = SourceDeduplicator()
    dedup.add(_record("arxiv:2501.00001", arxiv_id="2501.00001", abstract="a"))
    dedup.add(_record("semantic_scholar:S1", arxiv_id="2501.00001", abstract="b"))
    assert len(dedup.values()) == 1


def test_same_title_dedupes_when_no_ids():
    dedup = SourceDeduplicator()
    dedup.add(_record("x1", title="A Great Paper", abstract="a"))
    dedup.add(_record("x2", title="A Great Paper", abstract="b"))
    assert len(dedup.values()) == 1


def test_different_papers_remain_separate():
    dedup = SourceDeduplicator()
    dedup.add(_record("x1", doi="10.1/a", title="Paper A"))
    dedup.add(_record("x2", doi="10.2/b", title="Paper B"))
    assert len(dedup.values()) == 2


def test_duplicate_count_tracks_merged_records():
    dedup = SourceDeduplicator()
    dedup.add(_record("x1", doi="10.1000/xyz", title="Same"))
    dedup.add(_record("x2", doi="10.1000/xyz", title="Same"))
    dedup.add(_record("x3", doi="10.1000/xyz", title="Same"))
    dedup.add(_record("x4", doi="10.9999/other", title="Other"))
    assert dedup.duplicate_count == 2
    assert len(dedup.values()) == 2
