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
Direct `assess --json` artifacts are accepted only when task suitability was
not requested; task-scoped evidence must use the compiler binding envelope so
the profile itself is available for verification.

Finding codes in assessment dimensions are exposed as warning codes. New codes
fail closed by default. An allow rule applies only to newly introduced codes;
a deny rule rejects a code even when it was already present in the baseline.
