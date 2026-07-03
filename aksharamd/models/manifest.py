from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version


def _get_version() -> str:
    try:
        return _pkg_version("aksharamd")
    except PackageNotFoundError:
        return "0.0.0.dev"


from pydantic import BaseModel, Field


class Manifest(BaseModel):
    source: str
    file_type: str = ""
    pages: int = 0
    chunks: int = 0
    images: int = 0
    tables: int = 0
    original_tokens: int = 0
    optimized_tokens: int = 0
    token_reduction_percent: float = 0.0
    duplicate_blocks_removed: int = 0
    headers_removed: int = 0
    footers_removed: int = 0
    readiness_score: int = 0
    confidence_notes: list[str] = Field(default_factory=list)
    elapsed_seconds: float = 0.0
    stage_timings: dict[str, float] = Field(default_factory=dict)
    ai_plugins_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    compiled_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    blocks_extracted: int = 0
    blocks_inferred: int = 0
    blocks_ambiguous: int = 0
    aksharamd_version: str = Field(default_factory=_get_version)
