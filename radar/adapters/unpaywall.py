"""Optional Unpaywall adapter: resolve open-access PDFs for DOI-backed works.

Free API, no key: requests only carry a contact email (the same
``CROSSREF_MAILTO`` setting Crossref/OpenAlex already reuse).
"""

import tempfile
import time
from pathlib import Path

import httpx

from radar.config import get_settings
from radar.parsers.pdf import PdfParser


MAX_RETRIES = 3
MAX_PDF_BYTES = 30 * 1024 * 1024
MIN_FULL_TEXT_CHARS = 500


class UnpaywallAdapter:
    endpoint = "https://api.unpaywall.org/v2"

    def __init__(self, timeout_seconds: float = 30.0, *, email: str | None = None):
        self.timeout_seconds = timeout_seconds
        self.email = email or get_settings().crossref_mailto

    @staticmethod
    def _get_with_retries(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
        """GET with exponential backoff on 429/5xx and transient network
        failures such as timeouts (at most MAX_RETRIES retries)."""

        delay = 1.0
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = client.get(url, **kwargs)
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

    def _client(self, **overrides) -> httpx.Client:
        options = {
            "timeout": self.timeout_seconds,
            "headers": {"User-Agent": "ResearchRadar/0.1 local-demo"},
        }
        options.update(overrides)
        return httpx.Client(**options)

    def _fetch_payload(self, doi: str) -> dict:
        """Return the Unpaywall record for one DOI; {} when the DOI is unknown."""

        try:
            with self._client() as client:
                response = self._get_with_retries(
                    client, f"{self.endpoint}/{doi}", params={"email": self.email}
                )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Unpaywall answers 404 for DOIs it does not track; that is a
                # plain miss, not a lookup failure.
                return {}
            raise RuntimeError(f"unpaywall_lookup_failed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"unpaywall_lookup_failed: {exc}") from exc
        return response.json()

    def get_oa_pdf_url(self, doi: str) -> str | None:
        """Return the best open-access PDF URL for a DOI, or None."""

        payload = self._fetch_payload(doi)
        best_location = payload.get("best_oa_location") or {}
        return best_location.get("url_for_pdf") or None

    def download_pdf_text(self, pdf_url: str, *, max_chars: int = 800_000) -> str:
        """Download one open-access PDF and parse it to text.

        Raises RuntimeError on download, size, or parse failures so callers
        can degrade to abstract-level comparison.
        """

        try:
            with self._client(
                timeout=max(self.timeout_seconds, 30.0), follow_redirects=True
            ) as client:
                response = self._get_with_retries(client, pdf_url)
                if len(response.content) > MAX_PDF_BYTES:
                    raise RuntimeError("unpaywall_pdf_too_large")
        except httpx.HTTPError as exc:
            raise RuntimeError(f"unpaywall_pdf_download_failed: {exc}") from exc
        text = self._parse_pdf_bytes(response.content)
        if len(text) < MIN_FULL_TEXT_CHARS:
            raise RuntimeError("unpaywall_pdf_has_insufficient_text")
        return text[:max_chars]

    @staticmethod
    def _parse_pdf_bytes(content: bytes) -> str:
        """Parse PDF bytes through the standard PdfParser via a temp file."""

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
                handle.write(content)
                temp_path = Path(handle.name)
            return PdfParser().parse(temp_path).full_text
        except Exception as exc:
            raise RuntimeError(f"unpaywall_pdf_parse_failed: {exc}") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
