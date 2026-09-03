"""Reusable mathematical operations for embedding vectors."""

from __future__ import annotations

import math
from typing import Sequence


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Return the cosine similarity of two non-empty, equally sized vectors."""
    if len(left) != len(right) or not left:
        raise ValueError("Embeddings must have the same non-zero length.")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def average_embeddings(embeddings: Sequence[Sequence[float]]) -> list[float]:
    """Return the component-wise mean of compatible embedding vectors."""
    if not embeddings or len({len(item) for item in embeddings}) != 1:
        raise ValueError("Embeddings must be non-empty and have matching sizes.")
    return [sum(values) / len(values) for values in zip(*embeddings)]


def normalize_embedding(embedding: Sequence[float]) -> list[float]:
    """Return one finite, non-zero embedding with unit L2 norm."""
    if not embedding or not all(math.isfinite(value) for value in embedding):
        raise ValueError("Embedding must contain finite values.")
    norm = math.sqrt(sum(value * value for value in embedding))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("Embedding must have a non-zero finite norm.")
    return [value / norm for value in embedding]
