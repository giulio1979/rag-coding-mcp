"""Server-side embeddings over `name + description`.

Uses `fastembed` with ONNX Runtime (CPU only). The model is lazy-loaded on
first use so tests and short-lived processes don't pay the download/init cost
unnecessarily.
"""

from __future__ import annotations

import struct
from typing import Protocol

DEFAULT_MODEL_NAME = "BAAI/bge-small-en-v1.5"
DEFAULT_DIM = 384


class EmbedderProtocol(Protocol):
    model_name: str
    dim: int

    def embed(self, texts: list[str]) -> list[bytes]: ...

    def embed_source(self, name: str, description: str) -> bytes: ...


def encode_vector(vector: list[float]) -> bytes:
    """Pack a float32 vector into a compact bytes blob."""
    return struct.pack(f"{len(vector)}f", *vector)


def decode_vector(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def build_source_text(name: str, description: str) -> str:
    """Canonical form embedded for every node."""
    return name + "\n" + (description or "")


class FastEmbedEmbedder:
    """`fastembed` wrapper. Model is lazy-initialised on first `embed(...)` call."""

    def __init__(
        self, model_name: str = DEFAULT_MODEL_NAME, dim: int = DEFAULT_DIM
    ) -> None:
        self.model_name = model_name
        self.dim = dim
        self._model: object | None = None

    def _ensure(self) -> object:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[bytes]:
        if not texts:
            return []
        model = self._ensure()
        vectors = list(model.embed(texts))  # type: ignore[attr-defined]
        out: list[bytes] = []
        for v in vectors:
            as_list = v.tolist() if hasattr(v, "tolist") else list(v)
            if len(as_list) != self.dim:
                raise RuntimeError(
                    f"embedding returned {len(as_list)} dims, expected {self.dim}"
                )
            out.append(encode_vector(as_list))
        return out

    def embed_source(self, name: str, description: str) -> bytes:
        return self.embed([build_source_text(name, description)])[0]
