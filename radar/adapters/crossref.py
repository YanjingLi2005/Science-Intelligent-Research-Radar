"""Optional Crossref DOI integrity lookup."""

import httpx

from radar.config import get_settings


class CrossrefIntegrityAdapter:
    endpoint = "https://api.crossref.org/works"

    def __init__(
        self, timeout_seconds: float = 15.0, *, mailto: str | None = None
    ):
        self.timeout_seconds = timeout_seconds
        self.mailto = mailto or get_settings().crossref_mailto

    def _message(self, doi: str) -> dict:
        try:
            response = httpx.get(
                f"{self.endpoint}/{doi}", timeout=self.timeout_seconds,
                headers={
                    "User-Agent": f"ResearchRadar/0.1 (mailto:{self.mailto})"
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"crossref_check_failed: {exc}") from exc
        return response.json().get("message", {})

    # Crossref update types → integrity state (from the Crossref docs:
    # update-to/updated-by entries carry a "type" like retraction,
    # withdrawal, erratum, corrigendum, expression-of-concern, addendum...).
    _RETRACTED_TYPES = {"retraction", "withdrawal", "retract-and-replace"}
    _CONCERN_TYPES = {"expression-of-concern", "expression_of_concern"}
    _CORRECTED_TYPES = {"correction", "corrigendum", "erratum", "addendum"}

    def check(self, doi: str) -> dict:
        message = self._message(doi)
        updates = message.get("update-to", []) + message.get("updated-by", [])
        relation = message.get("relation", {})
        # Classify by the update entry's TYPE, never by substring-searching
        # the serialized payload: "correct" matches "incorrect", and types
        # like withdrawal/erratum were silently missed.
        entry_types = {
            str(entry.get("type", "")).strip().lower()
            for entry in updates
            if isinstance(entry, dict) and entry.get("type")
        }
        if entry_types & self._RETRACTED_TYPES:
            state = "retracted"
        elif entry_types & self._CONCERN_TYPES:
            state = "expression_of_concern"
        elif entry_types & self._CORRECTED_TYPES:
            state = "corrected"
        else:
            state = "normal"
        return {"doi": doi, "integrity_state": state, "updates": updates, "relation": relation}

    def metadata(self, doi: str) -> dict:
        """Return bibliographic fields needed for a human-auditable paper card."""

        message = self._message(doi)
        container_titles = message.get("container-title") or []
        event = message.get("event") or {}
        venue = next((item.strip() for item in container_titles if item.strip()), None)
        venue = venue or event.get("name") or message.get("publisher")
        return {
            "doi": doi,
            "venue": venue,
            "crossref_type": message.get("type"),
            "publisher_url": message.get("URL"),
        }
