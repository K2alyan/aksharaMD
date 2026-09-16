"""B1a-5b.1 acquisition machinery for the frozen B1a-5a dry-selected 20 documents.

This subpackage attaches evidence to the pre-existing selected canonical
IDs; it never rewrites selection. DEV_PILOT_MANIFEST_V1.json stays
byte-stable; acquisition writes its own artifact chain rooted at
docs/evaluation/DEV_PILOT_ACQUISITION_V1.json (an aggregate index over
one immutable per-document receipt).

Boundaries:

- No download URLs, payload hashes, acquisition status, retry counts,
  or timestamps are ever written into the selection manifest.
- Acquisition reuses the existing corpus-specific primitives in
  ``benchmarks.eval_v1.acquisition.pmc_oa_aws``,
  ``benchmarks.eval_v1.acquisition.doclaynet_hf``, and
  ``benchmarks.eval_v1.acquisition.federal_register_api``.
- No canonical ID is ever swapped, and no eligibility rule is relaxed
  in flight.
- On any terminal failure (identity, integrity, upstream-state change,
  or retry exhaustion), the run STOPS. Recovery is a separate human
  decision.
"""
