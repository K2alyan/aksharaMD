"""Document-clustered bootstrap for V1 estimand CIs.

PROTOCOL_V1.md §11.2: the analysis must **bootstrap by document, not by
observation**. Parser outputs on the same document are not independent
(shared underlying difficulty); resampling 800 observations pretends
they are and inflates confidence.

Two orthogonal design decisions matter for correctness:

1. **Aggregation semantics** — once documents are resampled, how are
   multiple parser observations on the same document combined into a
   single per-resample statistic?

   - ``aggregation="per_document"`` (default; matches §11.2's
     "document = resampling unit" framing): the estimator is applied
     per-document, and the resample statistic is the mean of those
     per-document estimates. Every document contributes equal weight
     regardless of how many parsers ran successfully on it.
   - ``aggregation="pooled"``: all observations from all drawn documents
     are concatenated and the estimator is applied to the flat list.
     Documents with more observations get more weight. Only defensible
     when the parser matrix is balanced.

2. **Balanced parser matrix enforcement** — B1's per-document
   observation lists are the ~4 parser outputs (Docling, Marker,
   MinerU, olmOCR). If any parser is missing for any document, silently
   flattening reweights the estimator. ``require_balanced=True``
   (default) refuses to proceed on an unbalanced input; the caller must
   either drop the affected documents, treat missing parsers as an
   outcome (see §11.2 non-blocking follow-up), or explicitly opt in via
   ``require_balanced=False``. When opted in, the imbalance is exposed
   on the result as ``parser_matrix_balanced=False`` and per-document
   observation counts, so no downstream reader can miss it.

Under A.1c this module was plumbing only. B1a is the first authorized
use, so the tightened semantics land here before any real corpus flows
through the estimator.
"""
from __future__ import annotations

import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

DEFAULT_N_RESAMPLES = 10_000

Aggregation = Literal["per_document", "pooled"]


@dataclass(frozen=True)
class BootstrapResult:
    estimator_value: float
    ci_low: float
    ci_high: float
    n_documents: int
    n_observations: int
    n_resamples: int
    ci_percent: float
    degenerate: bool
    aggregation: str
    parser_matrix_balanced: bool
    observations_per_document: tuple[int, ...]


class UnbalancedParserMatrixError(ValueError):
    """Raised when ``bootstrap_by_document`` is called with an unbalanced
    per-document observation matrix and ``require_balanced=True``.

    Distinct from generic ``ValueError`` so callers can distinguish a
    protocol-mandated stop from an argument mistake.
    """


def _score(
    drawn_docs: Sequence[str],
    observations_by_doc: dict[str, list[float]],
    estimator: Callable[[list[float]], float],
    aggregation: Aggregation,
) -> float | None:
    if aggregation == "per_document":
        per_doc: list[float] = []
        for d in drawn_docs:
            obs = observations_by_doc.get(d, [])
            if obs:
                per_doc.append(estimator(obs))
        if not per_doc:
            return None
        return statistics.mean(per_doc)
    flat: list[float] = []
    for d in drawn_docs:
        flat.extend(observations_by_doc.get(d, []))
    if not flat:
        return None
    return estimator(flat)


def bootstrap_by_document(
    documents: Sequence[str],
    observations_by_doc: dict[str, list[float]],
    estimator: Callable[[list[float]], float] = statistics.mean,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    ci_percent: float = 95.0,
    seed: int | None = 0,
    aggregation: Aggregation = "per_document",
    require_balanced: bool = True,
) -> BootstrapResult:
    """Bootstrap an estimand where the resampling unit is a document.

    ``observations_by_doc[doc_id]`` is the list of per-parser (or other
    per-observation) numeric outputs for that document. On each
    resample, ``len(documents)`` documents are drawn with replacement,
    and a per-resample scalar is computed under ``aggregation`` (see
    module docstring).

    Naive per-observation bootstrap is explicitly rejected — see §11.2.

    Missing observations never silently reweight:

    - ``require_balanced=True`` (default) raises
      :class:`UnbalancedParserMatrixError` if any document has an
      observation count different from the others (including zero).
    - ``require_balanced=False`` proceeds but records the imbalance on
      the result via ``parser_matrix_balanced`` and
      ``observations_per_document``.

    When ``len(documents)`` is small (e.g. 3, as in the A.1 smoke
    rerun), the CI is degenerate. The result carries a ``degenerate``
    flag so callers can present it honestly.
    """
    n_docs = len(documents)
    if n_docs == 0:
        raise ValueError("bootstrap_by_document requires at least one document")

    counts_per_doc = tuple(len(observations_by_doc.get(d, [])) for d in documents)
    total_obs = sum(counts_per_doc)
    unique_counts = set(counts_per_doc)
    balanced = len(unique_counts) == 1 and 0 not in unique_counts

    if require_balanced and not balanced:
        raise UnbalancedParserMatrixError(
            "bootstrap_by_document received an unbalanced observation matrix "
            f"(counts_per_document={counts_per_doc}). "
            "PROTOCOL_V1.md §11.2 requires the parser matrix be explicitly "
            "balanced, or the analyst must opt in to require_balanced=False "
            "so the imbalance is recorded on the BootstrapResult and no "
            "silent reweighting occurs."
        )

    point_maybe = _score(list(documents), observations_by_doc, estimator, aggregation)
    point = float("nan") if point_maybe is None else point_maybe

    rng = random.Random(seed)
    samples: list[float] = []
    doc_list = list(documents)
    for _ in range(n_resamples):
        drawn = [rng.choice(doc_list) for _ in range(n_docs)]
        s = _score(drawn, observations_by_doc, estimator, aggregation)
        if s is not None:
            samples.append(s)

    if not samples:
        return BootstrapResult(
            estimator_value=point,
            ci_low=float("nan"),
            ci_high=float("nan"),
            n_documents=n_docs,
            n_observations=total_obs,
            n_resamples=0,
            ci_percent=ci_percent,
            degenerate=True,
            aggregation=aggregation,
            parser_matrix_balanced=balanced,
            observations_per_document=counts_per_doc,
        )
    samples.sort()
    lo_idx = int((0.5 - ci_percent / 200.0) * len(samples))
    hi_idx = min(len(samples) - 1, int((0.5 + ci_percent / 200.0) * len(samples)))
    return BootstrapResult(
        estimator_value=point,
        ci_low=samples[max(0, lo_idx)],
        ci_high=samples[hi_idx],
        n_documents=n_docs,
        n_observations=total_obs,
        n_resamples=n_resamples,
        ci_percent=ci_percent,
        degenerate=(n_docs < 20),
        aggregation=aggregation,
        parser_matrix_balanced=balanced,
        observations_per_document=counts_per_doc,
    )


def per_observation_bootstrap_forbidden(*_a: object, **_kw: object) -> None:
    """Explicit trap: the naive per-observation bootstrap is prohibited
    by PROTOCOL_V1.md §11.2. Any caller reaching this function should
    switch to ``bootstrap_by_document``.
    """
    raise RuntimeError(
        "per-observation bootstrap is prohibited by PROTOCOL_V1.md §11.2 — "
        "use bootstrap_by_document instead."
    )
