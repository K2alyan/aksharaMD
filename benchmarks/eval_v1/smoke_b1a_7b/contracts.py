"""Load + hash the two merged contract JSON files.

The parser-execution contract config and the smoke-spec config are
loaded from disk at smoke start. Each is hashed two ways:

- Canonical-JSON SHA-256: parse -> re-serialize with sort_keys + no
  whitespace -> hash. Independent of on-disk line endings and
  formatting; used as the drift anchor in ``execution_record``.
- Raw-bytes SHA-256: hash of the file bytes on disk. Used only for
  logs / audit records; canonical is the load-bearing anchor.

The loader refuses to accept a config that disagrees with itself on
the pinned versions this harness is built against.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PARSER_EXECUTION_CONTRACT_PATH = Path(
    "benchmarks/eval_v1/config/parser_execution_contract_v1.json"
)
SMOKE_SPEC_PATH = Path("benchmarks/eval_v1/config/smoke_spec_v1.json")


class ContractError(RuntimeError):
    """Contract cannot be loaded, is inconsistent, or drifts at load
    time. Fail-closed for every downstream check."""


@dataclass(frozen=True)
class LoadedContract:
    path: Path
    obj: dict[str, Any]
    canonical_sha256: str
    raw_bytes_sha256: str


def _canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_contract(path: Path) -> LoadedContract:
    if not path.exists():
        raise ContractError(f"contract missing: {path}")
    raw = path.read_bytes()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError(f"contract not valid JSON: {path}: {exc}") from exc
    canonical_bytes = _canonical_json(obj)
    return LoadedContract(
        path=path,
        obj=obj,
        canonical_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
        raw_bytes_sha256=hashlib.sha256(raw).hexdigest(),
    )


@dataclass(frozen=True)
class ContractPair:
    parser_execution: LoadedContract
    smoke_spec: LoadedContract

    def parser_execution_contract_version(self) -> str:
        return self.parser_execution.obj["parser_execution_contract_version"]

    def smoke_spec_version(self) -> str:
        return self.smoke_spec.obj["smoke_spec_version"]

    def parsers(self) -> list[dict[str, Any]]:
        return list(self.parser_execution.obj["parsers"])

    def pinned_packages(self) -> dict[str, str]:
        return dict(self.parser_execution.obj["environment"]["packages_pinned"])

    def pinned_python_version(self) -> str:
        return self.parser_execution.obj["environment"]["python_pinned"]["version"]

    def platform_prefix(self) -> str:
        return self.parser_execution.obj["environment"]["os_pinned"][
            "primary_study_host_platform_prefix"
        ]

    def smoke_documents(self) -> list[dict[str, Any]]:
        return list(self.smoke_spec.obj["smoke_documents"]["documents"])

    def parser_level_defect_allowlist(self) -> set[str]:
        d = self.smoke_spec.obj["defect_handling"]
        return {reason for reason in d["parser_level_defect_reason_allowlist"]}

    def harness_or_env_defects(self) -> set[str]:
        d = self.smoke_spec.obj["defect_handling"]
        return {r for r in d["harness_or_environment_level_defects"]}

    def firewall_probe(self) -> dict[str, Any]:
        return dict(self.smoke_spec.obj["firewall_probe"])

    def review_allowlist(self) -> dict[str, Any]:
        return dict(self.smoke_spec.obj["review_allowlist"])


def load_contracts(
    parser_execution_path: Path | None = None,
    smoke_spec_path: Path | None = None,
) -> ContractPair:
    pe = load_contract(parser_execution_path or PARSER_EXECUTION_CONTRACT_PATH)
    ss = load_contract(smoke_spec_path or SMOKE_SPEC_PATH)

    if pe.obj.get("parser_execution_contract_version") != "v1":
        raise ContractError(
            "parser_execution_contract_version is not v1: "
            f"{pe.obj.get('parser_execution_contract_version')!r}"
        )
    if ss.obj.get("smoke_spec_version") != "v1":
        raise ContractError(
            f"smoke_spec_version is not v1: {ss.obj.get('smoke_spec_version')!r}"
        )
    if pe.obj.get("normalization_version") != "2":
        raise ContractError(
            "parser_execution_contract.normalization_version is not '2': "
            f"{pe.obj.get('normalization_version')!r}"
        )
    return ContractPair(parser_execution=pe, smoke_spec=ss)
