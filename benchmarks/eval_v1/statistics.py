"""Document-clustered bootstrap for V1 estimand CIs.

PROTOCOL_V1.md §11.2: the analysis must **bootstrap by document, not by
observation**. Parser outputs on the same document are not independent
(shared underlying difficulty); resampling 800 observations pretends
they are and inflates confidence.

Under A.1c this module is plumbing only. It is not invoked with any real
pilot corpus — the smoke rerun has N=3 documents so the bootstrap
returns degenerate CIs (which is honest). Live use awaits a downstream
authorization.
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence


DEFAULT_N_RESAMPLES = 10_000


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


def bootstrap_by_document(
    documents: Sequence[str],
    observations_by_doc: dict[str, list[float]],
    estimator: Callable[[list[float]], float] = statistics.mean,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    ci_percent: float = 95.0,
    seed: int | None = 0,
) -> BootstrapResult:
    """Bootstrap an estimand where the resampling unit is a document.

    ``observations_by_doc[doc_id]`` is the list of per-parser (or other
    per-observation) numeric outputs for that document. On each
    resample, ``len(documents)`` documents are drawn with replacement,
    all observations for each drawn document are concatenated, and the
    estimator is applied to that flat list.

    Naive per-observation bootstrap is explicitly rejected — see §11.2.

    When ``len(documents)`` is very small (e.g. 3, as in the A.1 smoke
    rerun), the CI is degenerate. The result carries a ``degenerate``
    flag so callers can present it honestly.
    """
    n_docs = len(documents)
    if n_docs == 0:
        raise ValueError("bootstrap_by_document requires at least one document")
    rng = random.Random(seed)
    all_obs: list[float] = []
    for d in documents:
        all_obs.extend(observations_by_doc.get(d, []))
    point = estimator(all_obs) if all_obs else float("nan")

    samples: list[float] = []
    for _ in range(n_resamples):
        drawn = [rng.choice(list(documents)) for _ in range(n_docs)]
        flat: list[float] = []
        for d in drawn:
            flat.extend(observations_by_doc.get(d, []))
        if flat:
            samples.append(estimator(flat))
    if not samples:
        return BootstrapResult(
            estimator_value=point,
            ci_low=float("nan"),
            ci_high=float("nan"),
            n_documents=n_docs,
            n_observations=len(all_obs),
            n_resamples=0,
            ci_percent=ci_percent,
            degenerate=True,
        )
    samples.sort()
    lo_idx = int((0.5 - ci_percent / 200.0) * len(samples))
    hi_idx = min(len(samples) - 1, int((0.5 + ci_percent / 200.0) * len(samples)))
    return BootstrapResult(
        estimator_value=point,
        ci_low=samples[max(0, lo_idx)],
        ci_high=samples[hi_idx],
        n_documents=n_docs,
        n_observations=len(all_obs),
        n_resamples=n_resamples,
        ci_percent=ci_percent,
        degenerate=(n_docs < 20),
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
