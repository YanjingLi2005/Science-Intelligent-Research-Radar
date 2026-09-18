"""Single structured-response LLM protocol."""

from collections.abc import Iterator
from typing import Protocol, TypeVar

from pydantic import BaseModel


ResponseT = TypeVar("ResponseT", bound=BaseModel)


class LLMClient(Protocol):
    def generate_structured(
        self,
        *,
        stage: str,
        prompt: str,
        response_model: type[ResponseT],
        max_tokens: int | None = None,
    ) -> ResponseT: ...

    def stream_text(
        self,
        *,
        stage: str,
        prompt: str,
        max_tokens: int | None = None,
    ) -> Iterator[str]: ...


