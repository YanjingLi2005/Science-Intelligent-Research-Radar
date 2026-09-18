"""Persistent vector store backed by ChromaDB for semantic paper retrieval."""

import hashlib
import json
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np


_DEFAULT_COLLECTION = "source_snapshots"


@runtime_checkable
class VectorStore(Protocol):
    def add(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict] | None = None,
        embeddings: list | None = None,
    ) -> None: ...
    def query(self, query_embedding: list[float], top_k: int = 20) -> list[tuple[str, float]]: ...
    def delete(self, ids: list[str]) -> None: ...
    def count(self) -> int: ...
    def clear(self) -> None:...


class MemoryVectorStore:
    """In-memory fallback when ChromaDB is not installed."""

    def __init__(self):
        self._ids: list[str] = []
        self._documents: list[str] = []
        self._embeddings: list[np.ndarray] = []

    def add(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict] | None = None,
        embeddings: list | None = None,
    ) -> None:
        self._ids.extend(ids)
        self._documents.extend(documents)
        if embeddings is not None:
            # The caller (RetrievalService) computes embeddings with the
            # configured client — without them the store cannot answer
            # queries, and silently returning [] hid that.
            self._embeddings.extend(
                np.asarray(embedding, dtype=np.float32) for embedding in embeddings
            )

    def query(self, query_embedding: list[float], top_k: int = 20) -> list[tuple[str, float]]:
        if not self._embeddings:
            return []
        q = np.asarray(query_embedding, dtype=np.float32)
        scores = np.asarray([float(q @ e) for e in self._embeddings])
        top = min(top_k, len(scores))
        if top == 0:
            return []
        indices = np.argpartition(-scores, top - 1)[:top]
        ranked = sorted(
            [(self._ids[int(i)], float(scores[int(i)])) for i in indices],
            key=lambda x: -x[1],
        )
        return ranked

    def set_embeddings(self, ids: list[str], embeddings: list[np.ndarray]) -> None:
        self._ids = list(ids)
        self._embeddings = list(embeddings)

    def delete(self, ids: list[str]) -> None:
        to_remove = set(ids)
        keep = [(i, d, e) for i, d, e in zip(self._ids, self._documents, self._embeddings) if i not in to_remove]
        if keep:
            self._ids, self._documents, self._embeddings = zip(*keep)
            self._ids, self._documents, self._embeddings = list(self._ids), list(self._documents), list(self._embeddings)
        else:
            self._ids, self._documents, self._embeddings = [], [], []

    def count(self) -> int:
        return len(self._ids)

    def clear(self) -> None:
        self._ids.clear()
        self._documents.clear()
        self._embeddings.clear()


def _chroma_available() -> bool:
    try:
        import chromadb  # noqa: F401
        return True
    except ImportError:
        return False


class ChromaVectorStore:
    """Persistent vector store backed by ChromaDB."""

    def __init__(self, persist_dir: str | Path, collection_name: str = _DEFAULT_COLLECTION):
        import chromadb
        self._persist_dir = str(persist_dir)
        Path(self._persist_dir).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=self._persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
            # Embeddings are supplied by the configured provider. Passing
            # None prevents Chroma from silently selecting its built-in ONNX
            # model and downloading it at first write.
            embedding_function=None,
        )

    def add(
        self,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict] | None = None,
        embeddings: list | None = None,
    ) -> None:
        if not ids:
            return
        if embeddings is None:
            raise ValueError("chroma_embeddings_required")
        content_hashes = [hashlib.sha256(doc.encode()).hexdigest() for doc in documents]
        # Copy the caller's metadata dicts: writing _content_hash must not
        # mutate shared state.
        metadatas = [dict(meta or {}) for meta in (metadatas or [{} for _ in ids])]
        for i, meta in enumerate(metadatas):
            meta["_content_hash"] = content_hashes[i]
        kwargs: dict = {"ids": ids, "documents": documents, "metadatas": metadatas}
        if embeddings is not None:
            # Explicit embeddings from the configured client — never Chroma's
            # default ONNX embedding function (first use downloads a model and
            # may be absent in slim installs).
            kwargs["embeddings"] = [
                embedding.tolist() if hasattr(embedding, "tolist") else embedding
                for embedding in embeddings
            ]
        # upsert: snapshot ids are stable across scans (abstract-hash identity,
        # see C2) so re-indexing the same snapshot must replace, not crash
        # with Chroma's DuplicateIDError.
        self._collection.upsert(**kwargs)

    def query(self, query_embedding: list[float], top_k: int = 20) -> list[tuple[str, float]]:
        if self._collection.count() == 0:
            return []
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, self._collection.count()),
            include=["distances"],
        )
        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        scores = [1.0 - float(d) for d in distances]
        return list(zip(ids, scores))

    def delete(self, ids: list[str]) -> None:
        if ids and self._collection.count() > 0:
            self._collection.delete(ids=ids)

    def count(self) -> int:
        return self._collection.count()

    def clear(self) -> None:
        existing = self._collection.get() if self._collection.count() > 0 else {"ids": []}
        if existing["ids"]:
            self._collection.delete(ids=existing["ids"])

    def snapshot_ids(self) -> list[str]:
        if self._collection.count() == 0:
            return []
        return self._collection.get()["ids"]

    def get_metadata(self, doc_id: str) -> dict | None:
        try:
            result = self._collection.get(ids=[doc_id], include=["metadatas"])
            if result["ids"]:
                return result["metadatas"][0]
        except Exception:
            pass
        return None


def build_vector_store(
    persist_dir: str | Path,
    collection_name: str = _DEFAULT_COLLECTION,
    *,
    prefer_chroma: bool = True,
) -> VectorStore:
    if prefer_chroma and _chroma_available():
        return ChromaVectorStore(persist_dir, collection_name)
    return MemoryVectorStore()
