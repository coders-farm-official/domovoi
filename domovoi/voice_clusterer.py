"""Single-link cosine clustering for buffered unknown-voice utterances.

When a third-party intro ("Domovoi, this is Alex") is active, every
unknown-voice turn during the window is appended to one of N clusters
(or starts a new one). The classifier later scores each cluster against
the introduction transcript to find the most likely Alex; the introducer
confirms.

Single-link is overkill-cheap for our scale (a handful of turns over a
few minutes), but it cleanly separates two distinct unknown voices —
say, Alex AND a delivery person who walks through during the window —
so the classifier doesn't have to disambiguate from a mixed-content
transcript.

The on-disk shape is JSON-serializable for sessions.context storage:

    {
      "centroid_b64": "<256 float32, base64>",
      "samples": [
        {"embedding_b64": "...", "transcript": "...", "audio_seconds": 1.4},
        ...
      ],
      "total_audio_seconds": 1.4,
    }
"""

from __future__ import annotations

import base64
import logging

import numpy as np

from domovoi.clients.voice_embedder import EMBEDDING_DIM

log = logging.getLogger(__name__)


def _decode(b64: str) -> np.ndarray | None:
    try:
        raw = base64.b64decode(b64.encode("ascii"))
        arr = np.frombuffer(raw, dtype=np.float32)
        return arr if arr.size == EMBEDDING_DIM else None
    except Exception:
        return None


def _encode(emb: np.ndarray) -> str:
    return base64.b64encode(emb.astype(np.float32).tobytes()).decode("ascii")


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def add_to_clusters(
    clusters: list[dict],
    embedding: np.ndarray,
    transcript: str,
    audio_seconds: float,
    merge_threshold: float = 0.7,
) -> list[dict]:
    """Mutate ``clusters`` to include the new sample, then return it.

    If the embedding's best cosine similarity against any existing
    cluster centroid beats ``merge_threshold``, the sample is appended
    to that cluster and its centroid is recomputed as the mean of all
    member embeddings (renormalized to unit length). Otherwise a fresh
    single-sample cluster is created.

    ``merge_threshold`` is intentionally looser than
    ``voice_profile_match_threshold`` (0.75) — at this stage we're
    deciding "are these the same unknown person" not "do they match a
    known profile," and we'd rather accidentally merge two members of
    the same family than split one person across two clusters.
    """
    new_sample = {
        "embedding_b64": _encode(embedding),
        "transcript": transcript,
        "audio_seconds": float(audio_seconds),
    }

    best_idx = -1
    best_sim = -1.0
    for i, cluster in enumerate(clusters):
        centroid = _decode(cluster.get("centroid_b64", ""))
        if centroid is None:
            continue
        sim = _cosine(embedding, centroid)
        if sim > best_sim:
            best_sim = sim
            best_idx = i

    if best_idx >= 0 and best_sim >= merge_threshold:
        cluster = clusters[best_idx]
        samples = cluster.setdefault("samples", [])
        samples.append(new_sample)
        embs: list[np.ndarray] = []
        for s in samples:
            e = _decode(s.get("embedding_b64", ""))
            if e is not None:
                embs.append(e)
        if embs:
            mean = np.mean(np.stack(embs), axis=0).astype(np.float32)
            n = float(np.linalg.norm(mean))
            if n > 0:
                mean = mean / n
            cluster["centroid_b64"] = _encode(mean)
        cluster["total_audio_seconds"] = sum(
            s.get("audio_seconds", 0.0) for s in samples
        )
        return clusters

    clusters.append(
        {
            "centroid_b64": _encode(embedding),
            "samples": [new_sample],
            "total_audio_seconds": float(audio_seconds),
        }
    )
    return clusters


def cluster_centroid(cluster: dict) -> np.ndarray | None:
    return _decode(cluster.get("centroid_b64", ""))


def cluster_transcript_text(cluster: dict, max_samples: int = 5) -> str:
    """Concatenate the most recent N sample transcripts for the LLM
    classifier. Capped to keep prompts short — the classifier mostly
    needs to see *enough* speech to judge consistency, not every word."""
    samples = cluster.get("samples", []) or []
    return " ".join(
        s.get("transcript", "")
        for s in samples[-max_samples:]
        if s.get("transcript")
    ).strip()
