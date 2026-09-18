"""Protocol for local or hosted text embedding providers."""

from typing import Protocol

import numpy as np


class EmbeddingClient(Protocol):
    model: str

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return one normalized vector per input text."""
        ...
