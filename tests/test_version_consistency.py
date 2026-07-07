"""
Fail fast when the package version drifts from human-edited doc files.

Rules enforced:
  - CHANGELOG.md must have a section header for the current version.
  - README.md "Current package is vX.Y.Z" must match.
  - benchmarks/LLM_QA_BENCHMARK.md "package version is vX.Y.Z" must match.

Add a new pattern here whenever a doc file is expected to track the version.
"""
from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _pkg_version() -> str:
    """Return version from pyproject.toml (the canonical source of truth).

    Falls back to importlib.metadata for installed packages so the test also
    works outside a development checkout.
    """
    toml_path = ROOT / "pyproject.toml"
    if toml_path.exists():
        import tomllib  # Python 3.11+
        with open(toml_path, "rb") as fh:
            data = tomllib.load(fh)
        return data["project"]["version"]
    return version("aksharamd")


# ── helpers ───────────────────────────────────────────────────────────────────

def _find(pattern: str, text: str) -> list[str]:
    return re.findall(pattern, text, re.IGNORECASE)


# ── tests ─────────────────────────────────────────────────────────────────────

def test_changelog_has_current_version():
    """CHANGELOG.md must contain a section for the current release."""
    pkg = _pkg_version()
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"[{pkg}]" in changelog or f"## {pkg}" in changelog, (
        f"CHANGELOG.md has no section for v{pkg}. "
        "Add a release entry or update the version in pyproject.toml."
    )


def test_readme_current_version_matches():
    """README.md 'Current package is vX.Y.Z' must match the installed version."""
    pkg = _pkg_version()
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    matches = _find(r"[Cc]urrent package is v([\d]+\.[\d]+\.[\d]+)", text)
    assert matches, (
        "README.md has no 'Current package is vX.Y.Z' line. "
        "Add one near the benchmark table or remove this assertion."
    )
    for found in matches:
        assert found == pkg, (
            f"README.md says 'Current package is v{found}' but installed version is v{pkg}. "
            "Update README.md to match pyproject.toml."
        )


def test_benchmark_doc_current_version_matches():
    """benchmarks/LLM_QA_BENCHMARK.md must reference the current package version."""
    pkg = _pkg_version()
    text = (ROOT / "benchmarks" / "LLM_QA_BENCHMARK.md").read_text(encoding="utf-8")
    matches = _find(r"package version is v([\d]+\.[\d]+\.[\d]+)", text)
    assert matches, (
        "benchmarks/LLM_QA_BENCHMARK.md has no 'package version is vX.Y.Z' line. "
        "Add one in the header callout block."
    )
    for found in matches:
        assert found == pkg, (
            f"LLM_QA_BENCHMARK.md says package version is v{found} "
            f"but installed version is v{pkg}. "
            "Update the benchmark doc header to match pyproject.toml."
        )
