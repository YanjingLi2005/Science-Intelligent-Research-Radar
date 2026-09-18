"""Citation-graph discovery, HyDE query enhancement and Unpaywall full text."""

import hashlib
from types import SimpleNamespace

import httpx
import numpy as np
from sqlalchemy import select

from radar.adapters.openalex import OpenAlexSearchAdapter
from radar.adapters.unpaywall import UnpaywallAdapter
from radar.models import (
    ManuscriptVersion,
    ResearchCase,
    ScanRun,
    Source,
    SourceSnapshot,
)
from radar.schemas import (
    ExtractedReference,
    ExtractedReferenceBatch,
    HydeAbstractOutput,
    SourceRecord,
)
from radar.services.weekly_radar_service import WeeklyRadarService

from test_openalex import OPENALEX_PAYLOAD, _json_response, _scripted_client
from test_weekly_radar import (
    INCOMING_QUOTE,
    MultiQuerySearch,
    OnePaperSearch,
    ScriptedLLM,
    _settings,
)


REFERENCES = [
    ExtractedReference(title="RadarNet: Robust Retrieval for DomainQA", year=2024),
    ExtractedReference(title="Unrelated Methods Survey", year=2020),
]

HYDE_TEXT = (
    "We present a retrieval robustness study on unseen-domain question answering "
    "with a matched BM25 baseline and exact match evaluation. "
    * 4
)
HYDE_NORMALIZED = " ".join(HYDE_TEXT.split())

# Bound at import time so tests can restore the real HTTP implementation over
# the offline autouse stub from conftest.
_ORIGINAL_FETCH_PAYLOAD = UnpaywallAdapter._fetch_payload


class EnhancementLLM(ScriptedLLM):
    """ScriptedLLM plus the degradable enhancement stages."""

    def __init__(
        self, *, references=None, hyde=HYDE_TEXT, fail_enhancements=False, **kwargs
    ):
        super().__init__(**kwargs)
        self.references = list(REFERENCES) if references is None else references
        self.hyde = hyde
        self.fail_enhancements = fail_enhancements
        self.enhancement_calls = 0

    def generate_structured(self, *, stage, prompt, response_model, max_tokens=None):
        if stage in {"reference_extraction", "hyde_abstract"}:
            self.enhancement_calls += 1
            if self.fail_enhancements:
                raise RuntimeError("llm_not_configured")
            if stage == "reference_extraction":
                return ExtractedReferenceBatch(references=self.references)
            return HydeAbstractOutput(abstract=self.hyde)
        return super().generate_structured(
            stage=stage, prompt=prompt, response_model=response_model
        )


class StubOpenAlex(OpenAlexSearchAdapter):
    """OpenAlex stand-in: no keyword results, scripted citation expansion."""

    def __init__(self, *, resolved=None, citing=None):
        self.resolved = dict(resolved or {})
        self.citing = dict(citing or {})

    def search(self, case_id, watch_query):
        return []

    def resolve_work(self, title):
        value = self.resolved.get(title)
        if isinstance(value, Exception):
            raise value
        return value

    def get_citing_works(self, openalex_id, *, since_days, max_results):
        return list(self.citing.get(openalex_id, []))


class FixedKeyword:
    def __init__(self, records):
        self.records = records

    def search(self, case_id, watch_query):
        return list(self.records)


class StubUnpaywall:
    def __init__(self, *, pdf_url=None, text=None):
        self.pdf_url = pdf_url
        self.text = text
        self.lookups = []

    def get_oa_pdf_url(self, doi):
        self.lookups.append(doi)
        return self.pdf_url

    def download_pdf_text(self, pdf_url):
        return self.text


class OnesEmbedding:
    def embed(self, texts):
        return np.ones((len(texts), 8), dtype=np.float32)


class RecordingVectorStore:
    """VectorStore double: records adds and answers queries from memory."""

    def __init__(self):
        self.added: list[tuple[list[str], list[str], list | None]] = []
        self.query_calls = 0
        self._embeddings: dict[str, np.ndarray] = {}
        self._documents: dict[str, str] = {}

    def add(self, ids, documents, metadatas=None, embeddings=None):
        self.added.append((ids, documents, embeddings))
        for doc_id, doc, emb in zip(ids, documents, embeddings if embeddings is not None else []):
            self._documents[doc_id] = doc
            self._embeddings[doc_id] = np.asarray(emb, dtype=np.float32)

    def query(self, query_embedding, top_k=20):
        self.query_calls += 1
        q = np.asarray(query_embedding, dtype=np.float32)
        scored = sorted(
            ((doc_id, float(q @ emb)) for doc_id, emb in self._embeddings.items()),
            key=lambda item: -item[1],
        )
        return scored[:top_k]

    def delete(self, ids):
        for doc_id in ids:
            self._embeddings.pop(doc_id, None)
            self._documents.pop(doc_id, None)

    def count(self):
        return len(self._embeddings)

    def clear(self):
        self._embeddings.clear()
        self._documents.clear()


def _record(external_id, *, doi=None, title="A Matched Re-evaluation of RadarNet"):
    return SourceRecord(
        external_id=external_id,
        title=title,
        authors=["Independent Lab"],
        abstract=INCOMING_QUOTE,
        url=f"https://example.org/{external_id}",
        published_at="2026-07-20T00:00:00Z",
        doi=doi,
    )


def _current_manuscript(db_session_factory, case_id):
    with db_session_factory() as session:
        manuscript = session.scalar(
            select(ManuscriptVersion).where(
                ManuscriptVersion.case_id == case_id,
                ManuscriptVersion.is_current.is_(True),
            )
        )
        session.expunge(manuscript)
        return manuscript


def _service(db_session_factory, **kwargs):
    kwargs.setdefault("settings", _settings())
    return WeeklyRadarService(db_session_factory, **kwargs)


def test_snapshot_identity_survives_full_text_enrichment(db_session_factory, golden_case):
    """C2 regression: content_hash stays the abstract identity across scans.

    Enriching a snapshot with full text must not make the next scan create a
    duplicate snapshot (which broke the P3 pair cache and grew the DB by a
    full-text row per paper per scan).
    """
    service = _service(db_session_factory, search_adapter=OnePaperSearch())
    record = _record("arxiv:identity-test", doi="10.5555/identity.test")

    first_ids = service._store_records([record])
    assert len(first_ids) == 1
    snapshot_id = first_ids[0]
    full_text = "Full text body of the paper. " * 40
    assert service._store_full_text(snapshot_id, full_text) is True

    # Second scan with the same abstract reuses the same snapshot (cache hit).
    assert service._store_records([record]) == [snapshot_id]

    with db_session_factory() as session:
        snapshot = session.get(SourceSnapshot, snapshot_id)
        assert snapshot.content_hash == hashlib.sha256(
            record.abstract.strip().encode()
        ).hexdigest()
        assert snapshot.full_text_hash == hashlib.sha256(full_text.encode()).hexdigest()
        assert snapshot.content_text == full_text  # enriched in place


# --- Task 1: reference extraction and citation-graph discovery --------------


def test_reference_extraction_cached_by_content_hash(db_session_factory, golden_case):
    llm = EnhancementLLM()
    service = _service(
        db_session_factory, search_adapter=OnePaperSearch(), llm_client=llm
    )
    manuscript = _current_manuscript(db_session_factory, golden_case)

    first = service._extracted_references(golden_case, manuscript)
    second = service._extracted_references(golden_case, manuscript)

    assert [reference.title for reference in first] == [
        reference.title for reference in REFERENCES
    ]
    assert second == first
    assert llm.enhancement_calls == 1
    with db_session_factory() as session:
        cached = session.get(ResearchCase, golden_case).settings_json[
            "extracted_references"
        ]
        assert cached["content_hash"] == manuscript.content_hash
        assert len(cached["references"]) == len(REFERENCES)

    # A changed manuscript content hash invalidates the cache.
    changed = SimpleNamespace(
        content_hash="different-hash", content_text=manuscript.content_text
    )
    service._extracted_references(golden_case, changed)
    assert llm.enhancement_calls == 2


def test_openalex_resolve_work_rejects_mismatched_title(monkeypatch):
    calls = []
    _scripted_client(
        monkeypatch,
        lambda index, url, kwargs: _json_response(200, OPENALEX_PAYLOAD, url),
        calls,
    )
    adapter = OpenAlexSearchAdapter(mailto="radar@example.org")

    assert adapter.resolve_work("A Completely Different Paper About Graphs") is None
    assert adapter.resolve_work("Retrieval Robustness at Scale") == "W123456"
    # Normalization ignores case and punctuation differences.
    assert adapter.resolve_work("retrieval robustness at scale!") == "W123456"


def test_openalex_get_citing_works_filters_and_maps(monkeypatch):
    calls = []
    _scripted_client(
        monkeypatch,
        lambda index, url, kwargs: _json_response(200, OPENALEX_PAYLOAD, url),
        calls,
    )
    adapter = OpenAlexSearchAdapter(mailto="radar@example.org")
    records = adapter.get_citing_works(
        "https://openalex.org/W123456", since_days=30, max_results=5
    )

    params = calls[0][1]["params"]
    assert params["filter"].startswith("cites:W123456,from_publication_date:")
    assert params["sort"] == "publication_date:desc"
    assert params["per-page"] == 5
    assert params["mailto"] == "radar@example.org"
    assert records[0].external_id == "openalex:W123456"
    assert records[0].title == "Retrieval Robustness at Scale"


def test_citation_graph_candidates_merge_and_dedupe(db_session_factory, golden_case):
    openalex = StubOpenAlex(
        resolved={"RadarNet: Robust Retrieval for DomainQA": "W999"},
        citing={
            "W999": [
                # Same DOI as the keyword hit: deduped, not counted as a hit.
                _record("openalex:c1", doi="10.1234/dup", title="Duplicate Hit"),
                _record("openalex:c2", title="Fresh Citing Paper"),
            ]
        },
    )
    service = _service(
        db_session_factory,
        search_adapters=[
            FixedKeyword([_record("arxiv:kw-1", doi="10.1234/dup")]),
            openalex,
        ],
        llm_client=EnhancementLLM(),
    )

    scan_id = service.run(
        golden_case, query="DomainQA RadarNet", max_results=10, analysis_limit=1
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        assert scan.stats_json["citation_discovery"] == "ok"
        assert scan.stats_json["citation_seeds"] == 1
        assert scan.stats_json["citation_hits"] == 1
        assert scan.stats_json["source_counts"]["citation_graph"] == 1
        assert scan.stats_json["scanned_papers"] == 2


def test_citation_seed_failure_skips_without_aborting(db_session_factory, golden_case):
    openalex = StubOpenAlex(
        resolved={
            "RadarNet: Robust Retrieval for DomainQA": RuntimeError("openalex boom"),
            "Unrelated Methods Survey": "W555",
        },
        citing={"W555": [_record("openalex:c9", title="Citing After Failure")]},
    )
    service = _service(
        db_session_factory,
        search_adapters=[FixedKeyword([_record("arxiv:kw-1")]), openalex],
        llm_client=EnhancementLLM(),
    )

    scan_id = service.run(
        golden_case, query="DomainQA RadarNet", max_results=10, analysis_limit=1
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        assert scan.stats_json["citation_discovery"] == "ok"
        assert scan.stats_json["citation_seeds"] == 1
        assert scan.stats_json["citation_hits"] == 1


def test_citation_graph_caps_total_candidates(db_session_factory, golden_case):
    from radar.services import weekly_radar_service as module

    openalex = StubOpenAlex(
        resolved={"RadarNet: Robust Retrieval for DomainQA": "W999"},
        citing={
            "W999": [
                _record(f"openalex:cap-{index}", title=f"Citing Paper {index}")
                for index in range(50)
            ]
        },
    )
    service = _service(
        db_session_factory,
        search_adapters=[FixedKeyword([_record("arxiv:kw-1")]), openalex],
        llm_client=EnhancementLLM(),
    )
    manuscript = _current_manuscript(db_session_factory, golden_case)
    original = module._CITATION_PER_SEED
    module._CITATION_PER_SEED = 40
    try:
        records, stats = service._citation_graph_candidates(golden_case, manuscript)
    finally:
        module._CITATION_PER_SEED = original
    assert len(records) == module._CITATION_MAX_TOTAL


# --- Task 2: HyDE query enhancement -----------------------------------------


def test_hyde_abstract_cached_by_question_and_claims(db_session_factory, golden_case):
    llm = EnhancementLLM()
    service = _service(
        db_session_factory, search_adapter=OnePaperSearch(), llm_client=llm
    )
    confirmed, _ = service._confirmed_claims(golden_case)

    first = service._hyde_abstract(golden_case, confirmed)
    second = service._hyde_abstract(golden_case, confirmed)

    assert first == HYDE_NORMALIZED
    assert second == first
    assert llm.enhancement_calls == 1


def test_hyde_query_joins_keyword_search_without_embedding(
    db_session_factory, golden_case
):
    search = MultiQuerySearch()
    service = _service(
        db_session_factory, search_adapter=search, llm_client=EnhancementLLM()
    )
    rank_queries: list[str] = []
    original_rank_pairs = service._rank_pairs

    def spy(snapshot_ids, confirmed, analysis_limit, *, query, **kwargs):
        rank_queries.extend([query] if isinstance(query, str) else query)
        return original_rank_pairs(
            snapshot_ids, confirmed, analysis_limit, query=query, **kwargs
        )

    service._rank_pairs = spy
    scan_id = service.run(
        golden_case, query="DomainQA RadarNet", max_results=5, analysis_limit=1
    )

    hyde_queries = [q for q in search.queries if HYDE_NORMALIZED[:60] in q]
    assert len(hyde_queries) == 1
    assert len(hyde_queries[0]) <= 200
    # Without embeddings only the keyword-search path uses the HyDE text.
    assert all(HYDE_NORMALIZED[:60] not in q for q in rank_queries)
    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.stats_json["hyde"] is True


def test_hyde_text_joins_ranking_queries_with_embedding(
    db_session_factory, golden_case
):
    service = _service(
        db_session_factory,
        search_adapter=OnePaperSearch(),
        llm_client=EnhancementLLM(),
    )
    service.retrieval.embedding_client = OnesEmbedding()
    ranked_queries: list[str] = []
    original_rank_sources = service.retrieval.rank_sources

    def spy(query, **kwargs):
        ranked_queries.append(query)
        return original_rank_sources(query, **kwargs)

    service.retrieval.rank_sources = spy
    scan_id = service.run(
        golden_case, query="DomainQA RadarNet", max_results=5, analysis_limit=1
    )

    assert HYDE_NORMALIZED in ranked_queries
    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.stats_json["hyde"] is True


# --- Task 3: Unpaywall full-text completion ---------------------------------


def test_unpaywall_get_oa_pdf_url_hit_and_miss(monkeypatch):
    adapter = UnpaywallAdapter(email="radar@example.org")
    monkeypatch.setattr(
        UnpaywallAdapter,
        "_fetch_payload",
        lambda self, doi: {
            "best_oa_location": {"url_for_pdf": "https://oa.example/paper.pdf"}
        },
    )
    assert adapter.get_oa_pdf_url("10.1/x") == "https://oa.example/paper.pdf"
    monkeypatch.setattr(
        UnpaywallAdapter, "_fetch_payload", lambda self, doi: {"best_oa_location": None}
    )
    assert adapter.get_oa_pdf_url("10.1/x") is None
    monkeypatch.setattr(UnpaywallAdapter, "_fetch_payload", lambda self, doi: {})
    assert adapter.get_oa_pdf_url("10.1/x") is None


def test_unpaywall_unknown_doi_is_a_plain_miss(monkeypatch):
    calls = []
    _scripted_client(
        monkeypatch, lambda index, url, kwargs: _json_response(404, url=url), calls
    )
    monkeypatch.setattr(UnpaywallAdapter, "_fetch_payload", _ORIGINAL_FETCH_PAYLOAD)
    adapter = UnpaywallAdapter(email="radar@example.org")

    assert adapter.get_oa_pdf_url("10.1/unknown") is None
    assert calls[0][1]["params"] == {"email": "radar@example.org"}
    assert calls[0][0].endswith("/v2/10.1/unknown")


def test_unpaywall_download_pdf_text_parses_pdf(monkeypatch):
    calls = []
    _scripted_client(
        monkeypatch,
        lambda index, url, kwargs: httpx.Response(
            200, content=b"%PDF-fake", request=httpx.Request("GET", url)
        ),
        calls,
    )
    monkeypatch.setattr(
        UnpaywallAdapter,
        "_parse_pdf_bytes",
        staticmethod(lambda content: "full text " * 100),
    )
    text = UnpaywallAdapter(email="radar@example.org").download_pdf_text(
        "https://oa.example/paper.pdf"
    )
    assert text.startswith("full text")
    assert calls[0][0] == "https://oa.example/paper.pdf"


def test_unpaywall_full_text_replaces_abstract_for_oa_doi(
    db_session_factory, golden_case
):
    full_text = INCOMING_QUOTE + "\n\n" + "Detailed method section. " * 200
    unpaywall = StubUnpaywall(pdf_url="https://oa.example/paper.pdf", text=full_text)
    service = _service(
        db_session_factory,
        search_adapter=FixedKeyword([_record("openalex:oa-1", doi="10.5555/oa.paper")]),
        llm_client=EnhancementLLM(),
        unpaywall_adapter=unpaywall,
    )

    scan_id = service.run(
        golden_case, query="DomainQA RadarNet", max_results=5, analysis_limit=1
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        assert scan.stats_json["unpaywall_hits"] == 1
        assert scan.stats_json["full_text_papers"] == 1
        assert scan.stats_json["impact_candidates"] == 1
        snapshot = session.scalar(
            select(SourceSnapshot)
            .join(Source, Source.id == SourceSnapshot.source_id)
            .where(Source.doi == "10.5555/oa.paper")
        )
        assert "Detailed method section." in snapshot.content_text
        assert snapshot.version_label.endswith(":public-pdf")
    assert unpaywall.lookups == ["10.5555/oa.paper"]


def test_vector_store_is_indexed_after_full_text_enrichment(
    db_session_factory, golden_case
):
    """M32/M33: the persistent vector store must be indexed AFTER full-text
    enrichment, so its documents cover complete papers — and the ranking
    leg must actually query the store."""
    full_text = INCOMING_QUOTE + "\n\n" + "Detailed method section. " * 200
    unpaywall = StubUnpaywall(pdf_url="https://oa.example/paper.pdf", text=full_text)
    store = RecordingVectorStore()
    service = _service(
        db_session_factory,
        search_adapter=FixedKeyword([_record("openalex:oa-1", doi="10.5555/oa.paper")]),
        llm_client=EnhancementLLM(),
        unpaywall_adapter=unpaywall,
        embedding_client=OnesEmbedding(),
        vector_store=store,
    )

    scan_id = service.run(
        golden_case, query="DomainQA RadarNet", max_results=5, analysis_limit=1
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed", scan.error_message
    # The index holds the enriched full text, not the abstract-only snapshot.
    assert store.added
    all_documents = " ".join(doc for _, documents, _ in store.added for doc in documents)
    assert "Detailed method section." in all_documents
    assert store.query_calls > 0  # ranking actually queried the store


def test_unpaywall_miss_keeps_abstract_level_comparison(
    db_session_factory, golden_case
):
    unpaywall = StubUnpaywall(pdf_url=None)
    service = _service(
        db_session_factory,
        search_adapter=FixedKeyword([_record("openalex:oa-1", doi="10.5555/oa.paper")]),
        llm_client=EnhancementLLM(),
        unpaywall_adapter=unpaywall,
    )

    scan_id = service.run(
        golden_case, query="DomainQA RadarNet", max_results=5, analysis_limit=1
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        assert scan.stats_json["unpaywall_hits"] == 0
        assert scan.stats_json["full_text_papers"] == 0
        assert scan.stats_json["impact_candidates"] == 1


def test_unpaywall_download_failure_degrades_to_abstract(
    db_session_factory, golden_case
):
    class FailingDownload(StubUnpaywall):
        def download_pdf_text(self, pdf_url):
            raise RuntimeError("unpaywall_pdf_download_failed: boom")

    service = _service(
        db_session_factory,
        search_adapter=FixedKeyword([_record("openalex:oa-1", doi="10.5555/oa.paper")]),
        llm_client=EnhancementLLM(),
        unpaywall_adapter=FailingDownload(pdf_url="https://oa.example/paper.pdf"),
    )

    scan_id = service.run(
        golden_case, query="DomainQA RadarNet", max_results=5, analysis_limit=1
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        assert scan.stats_json["unpaywall_hits"] == 1
        assert scan.stats_json["full_text_failures"] == 1
        assert scan.stats_json["impact_candidates"] == 1


# --- Full-chain degradation without a configured LLM -------------------------


def test_enhancements_degrade_when_llm_unconfigured(db_session_factory, golden_case):
    service = _service(
        db_session_factory,
        search_adapters=[FixedKeyword([_record("arxiv:kw-1")]), StubOpenAlex()],
        llm_client=EnhancementLLM(fail_enhancements=True),
    )

    scan_id = service.run(
        golden_case, query="DomainQA RadarNet", max_results=5, analysis_limit=1
    )

    with db_session_factory() as session:
        scan = session.get(ScanRun, scan_id)
        assert scan.status == "completed"
        assert scan.stats_json["citation_discovery"] == "skipped:llm"
        assert scan.stats_json["citation_seeds"] == 0
        assert scan.stats_json["citation_hits"] == 0
        assert scan.stats_json["hyde"] is False
        # The core keyword pipeline is unaffected by enhancement failures.
        assert scan.stats_json["impact_candidates"] == 1
