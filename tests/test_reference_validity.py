"""Offline tests for Layer 1 reference parsing and validity checks."""

from radar.schemas import ReferenceEntry, SourceRecord
from radar.services.reference_parser import parse_bibtex, parse_plain_references
from radar.services.reference_validity_service import ReferenceValidityService


def test_parse_bibtex_extracts_common_fields():
    text = r"""
@article{smith2020,
  title = {A Deterministic Reference Check},
  author = {Smith, Jane and Doe, John},
  year = {2020},
  journal = {Journal of Testing},
  doi = {10.1234/reference.2020}
}
@inproceedings{lee2021,
  title = "Reliable Bibliography Parsing",
  author = "Lee, Alex and Wang, Mei",
  year = 2021,
  booktitle = {Proceedings of Testing},
  url = {https://doi.org/10.1234/parsing.2021}
}
"""

    entries = parse_bibtex(text)

    assert len(entries) == 2
    assert entries[0].title == "A Deterministic Reference Check"
    assert entries[0].authors == ["Smith, Jane", "Doe, John"]
    assert entries[0].doi == "10.1234/reference.2020"
    assert entries[0].year == 2020
    assert entries[1].title == "Reliable Bibliography Parsing"
    assert entries[1].authors == ["Lee, Alex", "Wang, Mei"]
    assert entries[1].doi == "10.1234/parsing.2021"
    assert entries[1].year == 2021


def test_parse_plain_references_numbered_list():
    text = (
        "[1] Smith, Jane and Doe, John (2020). A Deterministic Reference Check. "
        "Journal of Testing. DOI: 10.1234/reference.2020\n"
        "[2] Lee, Alex (2021). Reliable Bibliography Parsing. "
        "arXiv:2101.12345"
    )

    entries = parse_plain_references(text)

    assert len(entries) == 2
    assert entries[0].title == "A Deterministic Reference Check"
    assert entries[0].year == 2020
    assert entries[0].doi == "10.1234/reference.2020"
    assert entries[1].title == "Reliable Bibliography Parsing"
    assert entries[1].arxiv_id == "2101.12345"


def _record(*, doi: str, title: str) -> SourceRecord:
    return SourceRecord(
        source_kind="openalex",
        external_id="openalex:W123",
        title=title,
        authors=["Jane Smith", "John Doe"],
        abstract="",
        url="https://openalex.org/W123",
        published_at="2020-01-01",
        doi=doi,
        venue="Journal of Testing",
    )


class _OpenAlex:
    def __init__(self, record=None, error=False):
        self.record = record
        self.error = error

    def get_work_by_doi(self, doi):
        if self.error:
            raise RuntimeError("temporary failure")
        return self.record if self.record and self.record.doi == doi else None

    def get_work_by_title(self, title):
        return None


class _Crossref:
    def __init__(self, result=None):
        self.result = result

    def metadata(self, doi):
        return self.result(doi) if callable(self.result) else self.result


class _Miss:
    def resolve_by_title(self, title):
        return None


class _ArxivMiss:
    def lookup(self, arxiv_ids):
        return []


def _adapters(openalex, crossref=None):
    return {
        "openalex": openalex,
        "crossref": crossref or _Crossref(),
        "dblp": _Miss(),
        "arxiv": _ArxivMiss(),
    }


def test_reference_validity_verifies_exact_doi_and_title():
    entry = ReferenceEntry(
        title="A Deterministic Reference Check",
        authors=["Smith, Jane", "Doe, John"],
        year=2020,
        venue="Journal of Testing",
        doi="10.1234/reference.2020",
    )
    result = ReferenceValidityService(
        adapters=_adapters(_OpenAlex(_record(doi=entry.doi, title=entry.title)))
    ).check_entry(entry)

    assert result.status == "verified"
    assert result.confidence >= 0.7
    assert result.matched is not None
    assert "doi" in result.matched.matched_fields
    assert "doi.org" in result.matched.url or "openalex" in result.matched.url


def test_reference_validity_reports_a_miss():
    result = ReferenceValidityService(adapters=_adapters(_OpenAlex())).check_entry(
        ReferenceEntry(title="A Paper That Is Not Indexed")
    )

    assert result.status == "unverified"
    assert "not_found" in result.reasons


def test_reference_validity_batch_survives_one_adapter_failure():
    entries = [
        ReferenceEntry(title="First Paper", doi="10.1234/first"),
        ReferenceEntry(title="Second Paper", doi="10.1234/second"),
    ]
    service = ReferenceValidityService(
        adapters=_adapters(
            _OpenAlex(error=True),
            _Crossref(lambda doi: {"doi": doi}),
        )
    )

    batch = service.check_batch(entries)

    assert len(batch.results) == 2
    assert batch.summary == {"total": 2, "verified": 2, "unverified": 0}
    assert all(result.status == "verified" for result in batch.results)
    assert all("source_unavailable" in result.reasons for result in batch.results)
