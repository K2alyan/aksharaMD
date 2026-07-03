"""
aksharamd/dedup/minhash.py

Lightweight MinHash + LSH implementation for near-duplicate detection across
a corpus of compiled documents.  No numpy or external ML dependencies.

Algorithm:
  - Shingle each document's text into overlapping 5-word n-grams
  - Compute a 64-permutation MinHash signature using Mersenne-prime universal
    hashing (same family as Graphify's _minhash.py, compatible signatures)
  - Index signatures in an LSH structure with b bands × r rows optimised for
    the given Jaccard threshold
  - Query: find all previously indexed documents whose signature is in the same
    band bucket as the new document → near-duplicate candidates

Usage:
    from aksharamd.dedup.minhash import CorpusDeduplicator

    dd = CorpusDeduplicator(threshold=0.5)
    for doc_id, text in corpus:
        dupes = dd.add(doc_id, text)   # returns list of near-duplicate doc_ids
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict

# ── Constants ──────────────────────────────────────────────────────────────────

_MERSENNE_PRIME = (1 << 61) - 1
_MAX_HASH       = (1 << 32) - 1
_NUM_PERM       = 64          # permutations; 64 gives ±7% Jaccard estimation error
_SHINGLE_K      = 5           # word k-gram size
_WORD_RE        = re.compile(r"\w+")


# ── Mersenne-prime hash coefficients (generated once per _NUM_PERM) ────────────

def _gen_coeffs(num_perm: int) -> tuple[list[int], list[int]]:
    """Return (a_list, b_list) — one pair per permutation, seeded deterministically."""
    rng_a: list[int] = []
    rng_b: list[int] = []
    seen: set[int] = set()
    h = 0
    while len(rng_a) < num_perm:
        h = int(hashlib.sha1((h & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "big")).hexdigest(), 16)  # noqa: S324
        a = (h & _MAX_HASH) | 1          # must be odd
        b = ((h >> 32) & _MAX_HASH)
        if a not in seen:
            seen.add(a)
            rng_a.append(a)
            rng_b.append(b)
    return rng_a, rng_b


_A_COEFFS, _B_COEFFS = _gen_coeffs(_NUM_PERM)


# ── Shingling ──────────────────────────────────────────────────────────────────

def _shingles(text: str, k: int = _SHINGLE_K) -> list[bytes]:
    words = _WORD_RE.findall(text.lower())
    if len(words) < k:
        return [" ".join(words).encode()]
    return [" ".join(words[i : i + k]).encode() for i in range(len(words) - k + 1)]


# ── MinHash signature ──────────────────────────────────────────────────────────

def minhash(text: str, num_perm: int = _NUM_PERM) -> list[int]:
    """Return a list of *num_perm* minimum hash values for *text*."""
    sig = [_MAX_HASH] * num_perm
    shingles = _shingles(text)
    if not shingles:
        return sig
    for shingle in shingles:
        h = int(hashlib.sha1(shingle).digest()[:4].hex(), 16)  # noqa: S324
        for i in range(num_perm):
            phv = ((_A_COEFFS[i] * h + _B_COEFFS[i]) % _MERSENNE_PRIME) & _MAX_HASH
            if phv < sig[i]:
                sig[i] = phv
    return sig


# ── LSH band parameters ────────────────────────────────────────────────────────

def _lsh_params(threshold: float, num_perm: int) -> tuple[int, int]:
    """Find (b, r) that minimise combined false-positive + false-negative error
    at the given Jaccard threshold.  r = num_perm // b."""
    best: tuple[float, int, int] = (float("inf"), 1, num_perm)
    for b in range(1, num_perm + 1):
        if num_perm % b != 0:
            continue
        r = num_perm // b
        # Probability of sharing a bucket at similarity s
        # FP: integrate over [0, threshold), FN: integrate over [threshold, 1]
        # Approximate with discrete steps
        fp = sum(
            (1 - (s / 100) ** r) ** b
            for s in range(0, int(threshold * 100))
        ) / (int(threshold * 100) or 1)
        fn = sum(
            1 - (1 - (s / 100) ** r) ** b
            for s in range(int(threshold * 100), 100)
        ) / max(100 - int(threshold * 100), 1)
        err = fp + fn
        if err < best[0]:
            best = (err, b, r)
    return best[1], best[2]


# ── CorpusDeduplicator ────────────────────────────────────────────────────────

class CorpusDeduplicator:
    """Index documents as they arrive; return near-duplicate candidates for each.

    Args:
        threshold: Jaccard similarity above which two documents are considered
                   near-duplicates (default 0.5).
        num_perm:  Number of MinHash permutations (default 64).
    """

    def __init__(self, threshold: float = 0.5, num_perm: int = _NUM_PERM) -> None:
        self.threshold = threshold
        self.num_perm = num_perm
        self._b, self._r = _lsh_params(threshold, num_perm)
        # _tables[band_idx][band_key] = list of doc_ids
        self._tables: list[dict[tuple, list[str]]] = [
            defaultdict(list) for _ in range(self._b)
        ]
        self._sigs: dict[str, list[int]] = {}

    def signature(self, text: str) -> list[int]:
        return minhash(text, self.num_perm)

    def add(self, doc_id: str, text: str) -> list[str]:
        """Index *doc_id* and return all previously indexed near-duplicate ids."""
        sig = self.signature(text)
        candidates: set[str] = set()
        for band_idx in range(self._b):
            start = band_idx * self._r
            band_key = tuple(sig[start : start + self._r])
            bucket = self._tables[band_idx][band_key]
            candidates.update(bucket)
            bucket.append(doc_id)
        self._sigs[doc_id] = sig
        # Filter candidates by exact Jaccard estimate to reduce false positives
        return [c for c in candidates if c != doc_id and self._jaccard(sig, self._sigs[c]) >= self.threshold]

    def _jaccard(self, sig_a: list[int], sig_b: list[int]) -> float:
        matches = sum(a == b for a, b in zip(sig_a, sig_b))
        return matches / self.num_perm

    def already_seen(self, doc_id: str, text: str) -> bool:
        """Return True if *text* is a near-duplicate of any previously added document."""
        return bool(self.add(doc_id, text))

    @property
    def indexed_count(self) -> int:
        return len(self._sigs)
