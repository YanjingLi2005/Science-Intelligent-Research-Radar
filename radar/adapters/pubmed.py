"""PubMed search adapter via NCBI E-utilities.

Brings biomedical literature into the same SourceRecord contract. Abstracts
are fetched with efetch so records are usable by the normal storage pipeline.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from radar.schemas import SourceRecord, WatchQuery

MAX_RETRIES = 3
ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


class PubmedSearchAdapter:
    source_kind = "pubmed"

    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        email: str = "local@example.invalid",
        tool: str = "research-radar",
    ):
        self.timeout_seconds = timeout_seconds
        self.email = email
        self.tool = tool

    @staticmethod
    def _get_with_retries(client: httpx.Client, url: str, params: dict) -> httpx.Response:
        delay = 1.0
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = client.get(url, params=params)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if (status != 429 and status < 500) or attempt == MAX_RETRIES:
                    raise
                time.sleep(delay)
                delay *= 2
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")

    def _params(self, **extra: Any) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "email": self.email,
            **extra,
        }

    def search(self, case_id: str, watch_query: WatchQuery) -> list[SourceRecord]:
        del case_id  # Global API; case scoping is caller-side.
        pmids = self._search_ids(watch_query.query, watch_query.max_results)
        if not pmids:
            return []
        records = self._fetch_records(pmids)
        return records[: watch_query.max_results]

    def _search_ids(self, query: str, max_results: int) -> list[str]:
        params = self._params(
            db="pubmed",
            term=query,
            retmode="json",
            retmax=min(max_results, 50),
            sort="date",
        )
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = self._get_with_retries(client, ESEARCH, params)
                payload = response.json()
        except Exception as exc:
            raise RuntimeError(f"pubmed_search_failed: {exc}") from exc
        id_list = payload.get("esearchresult", {}).get("idlist", []) or []
        return [str(item) for item in id_list if str(item).strip()]

    def _fetch_records(self, pmids: list[str]) -> list[SourceRecord]:
        if not pmids:
            return []
        params = self._params(
            db="pubmed",
            id=",".join(pmids),
            rettype="abstract",
            retmode="xml",
        )
        try:
            with httpx.Client(timeout=max(self.timeout_seconds, 45.0)) as client:
                response = self._get_with_retries(client, EFETCH, params)
                xml_text = response.text
        except Exception as exc:
            raise RuntimeError(f"pubmed_fetch_failed: {exc}") from exc

        records: list[SourceRecord] = []
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise RuntimeError(f"pubmed_parse_failed: {exc}") from exc

        for article in root.findall(".//PubmedArticle"):
            record = self._parse_article(article)
            if record is not None:
                records.append(record)
        return records

    @staticmethod
    def _parse_article(article: ET.Element) -> SourceRecord | None:
        medline = article.find("MedlineCitation")
        if medline is None:
            return None
        pmid = medline.findtext("PMID") or ""
        article_el = medline.find("Article")
        if not pmid or article_el is None:
            return None

        title = " ".join((article_el.findtext("ArticleTitle") or "").split())
        abstract_parts = [
            "".join(node.itertext())
            for node in article_el.findall(".//AbstractText")
        ]
        abstract = " ".join(part.strip() for part in abstract_parts if part.strip())

        authors: list[str] = []
        for author in article_el.findall(".//AuthorList/Author"):
            last = author.findtext("LastName") or ""
            initials = author.findtext("Initials") or ""
            collective = author.findtext("CollectiveName") or ""
            if collective:
                authors.append(collective.strip())
            elif last or initials:
                authors.append(f"{last} {initials}".strip())

        journal = article_el.findtext(".//Journal/Title") or ""
        year = (
            article_el.findtext(".//Journal/JournalIssue/PubDate/Year")
            or article_el.findtext(".//Journal/JournalIssue/PubDate/MedlineDate")
            or ""
        )
        year = year[:4] if year else ""

        doi = ""
        for article_id in article_el.findall(".//ArticleIdList/ArticleId"):
            if (article_id.get("IdType") or "").lower() == "doi":
                doi = (article_id.text or "").strip()
                break

        mesh_terms: list[str] = []
        for descriptor in medline.findall(".//MeshHeadingList/MeshHeading/DescriptorName"):
            name = (descriptor.text or "").strip()
            if name:
                mesh_terms.append(name)

        published_at = f"{year}-01-01" if year else None
        return SourceRecord(
            source_kind="pubmed",
            external_id=f"pubmed:{pmid}",
            title=title or f"PubMed {pmid}",
            authors=authors,
            abstract=abstract,
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            published_at=published_at,
            doi=doi or None,
            arxiv_id=None,
            venue=journal or None,
            publication_type="journal_article",
            pdf_url=None,
            cited_by_count=None,
            arxiv_primary_category=None,
            fields_of_study=mesh_terms[:8],
            ccf_rank=None,
        )
