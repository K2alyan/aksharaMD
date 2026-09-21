# Parser regression gate

`aksharamd gate` is a local, deterministic CI gate over saved assessment
artifacts. It compares a parser release candidate with an accepted baseline; it
does not rerun parsers, call an LLM, or predict downstream QA accuracy.

Create a manifest whose paths are relative to the manifest:

```json
{
  "schema_version": "1.0",
  "policy": {
    "required_disposition": "ACCEPT",
    "required_invariants": [
      "schema_version", "policy_id", "source_hash", "task_profile_sha256"
    ],
    "deny_new_warnings": true,
    "allow_new_warning_codes": [],
    "deny_warning_codes": ["CRITICAL_LITERAL_MISSING"]
  },
  "comparisons": [
    {
      "id": "invoice-001",
      "baseline": "baseline/invoice-001/quality_assessment.json",
      "candidate": "candidate/invoice-001/quality_assessment.json"
    }
  ]
}
```

Run `aksharamd gate gate.json` for a concise report or add `--json` for a
stable machine-readable report. Exit code `0` means every comparison passed,
`2` means the policy denied at least one comparison, and `1` means an input was
invalid. Reports include SHA-256 identities for the manifest and both evidence
artifacts.

The default invariants also compare the canonical task-profile SHA-256. An
assessment with no task profile is bound to the explicit `none` sentinel.
Every versioned assessment result binds the canonical profile identity used by
the assessor. Compiler binding envelopes additionally carry the strict profile
payload; the gate recomputes its identity and rejects the artifact unless it
matches the assessment. Direct `assess --json` artifacts retain the bound
identity, while envelopes provide the stronger payload-to-evidence check.

Assessment results use schema `1.1`, which introduced the required
`task_profile_sha256` identity. The gate deliberately rejects assessment schema
`1.0`: those artifacts predate this provenance field, so treating them as
equivalent would reopen the profile-substitution bypass. Regenerate legacy
assessments before using them as a release baseline. The gate manifest,
compiler binding envelope, and task-profile schemas remain independently
versioned at `1.0`.

Finding codes in assessment dimensions are exposed as warning codes. New codes
fail closed by default. An allow rule applies only to newly introduced codes;
a deny rule rejects a code even when it was already present in the baseline.
