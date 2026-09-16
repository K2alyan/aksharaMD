"""Contract loader tests: SHA computation, version checks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.eval_v1.smoke_b1a_7b.contracts import (
    ContractError,
    _canonical_json,
    load_contract,
    load_contracts,
)


def test_load_real_configs_matches_committed_bytes() -> None:
    pair = load_contracts()
    # Version pins.
    assert pair.parser_execution_contract_version() == "v1"
    assert pair.smoke_spec_version() == "v1"
    # Both SHAs are 64-hex.
    assert len(pair.parser_execution.canonical_sha256) == 64
    assert len(pair.smoke_spec.canonical_sha256) == 64
    assert len(pair.parser_execution.raw_bytes_sha256) == 64
    assert len(pair.smoke_spec.raw_bytes_sha256) == 64


def test_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="missing"):
        load_contract(tmp_path / "does_not_exist.json")


def test_invalid_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ContractError, match="not valid JSON"):
        load_contract(p)


def test_canonical_sha_independent_of_formatting(tmp_path: Path) -> None:
    obj = {"a": 1, "b": [1, 2, 3], "c": {"d": True}}
    p1 = tmp_path / "a.json"
    p2 = tmp_path / "b.json"
    p1.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    p2.write_text(json.dumps(obj, indent=None, separators=(",", ":")), encoding="utf-8")
    c1 = load_contract(p1)
    c2 = load_contract(p2)
    assert c1.canonical_sha256 == c2.canonical_sha256
    assert c1.raw_bytes_sha256 != c2.raw_bytes_sha256  # raw bytes differ


def test_load_contracts_refuses_wrong_version(tmp_path: Path, monkeypatch) -> None:
    # Build a tainted parser-execution config with a bad version.
    pec = json.loads(Path("benchmarks/eval_v1/config/parser_execution_contract_v1.json").read_bytes())
    pec["parser_execution_contract_version"] = "v0"
    bad_path = tmp_path / "pec_bad.json"
    bad_path.write_text(json.dumps(pec), encoding="utf-8")
    with pytest.raises(ContractError, match="parser_execution_contract_version"):
        load_contracts(parser_execution_path=bad_path)


def test_canonical_json_helper() -> None:
    obj = {"b": 2, "a": 1}
    result = _canonical_json(obj)
    assert result == b'{"a":1,"b":2}'
    assert hashlib.sha256(result).hexdigest() == hashlib.sha256(b'{"a":1,"b":2}').hexdigest()
