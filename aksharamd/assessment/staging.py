"""Stage compiler output until an assessment explicitly accepts it.

The legacy compiler continues to write directly to its configured output
directory.  Opt-in callers create this transaction, compile into
``staging_dir``, and call :meth:`promote_if_accepted` only after reading the
assessment bound to those staged bytes.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .models import AssessmentDisposition


class PromotionError(RuntimeError):
    """Raised when a staged output cannot safely become the active output."""


class StagedOutput:
    """One same-volume output generation with explicit acceptance promotion.

    A target that already exists is deliberately never replaced.  Callers that
    need version replacement must provide a separate, tested activation layer;
    silently deleting or merging an existing output directory could expose a
    mixed generation.  Until promotion, the staged generation remains
    inspectable for review and can be removed by the caller.
    """

    def __init__(self, final_output_dir: str | Path) -> None:
        self.final_output_dir = Path(final_output_dir)
        parent = self.final_output_dir.parent
        parent.mkdir(parents=True, exist_ok=True)
        self.staging_dir = Path(
            tempfile.mkdtemp(prefix=f".{self.final_output_dir.name}.staging-", dir=parent)
        )
        self._promoted = False

    @property
    def promoted(self) -> bool:
        """Whether this generation has become the configured final output."""
        return self._promoted

    def promote_if_accepted(self, disposition: AssessmentDisposition | str) -> bool:
        """Atomically promote only an explicit ``ACCEPT`` disposition.

        ``False`` means no activation occurred.  The staging directory remains
        available for review in that case.  Promotion uses an atomic rename
        because staging and final directories share a parent and therefore a
        filesystem.  Existing final output is refused rather than replaced.
        """
        value = disposition.value if isinstance(disposition, AssessmentDisposition) else disposition
        if value != AssessmentDisposition.ACCEPT.value:
            return False
        if self._promoted:
            raise PromotionError("staged output has already been promoted")
        if not self.staging_dir.is_dir():
            raise PromotionError(f"staging output is unavailable: {self.staging_dir}")
        if self.final_output_dir.exists():
            raise PromotionError(
                f"refusing to replace existing final output: {self.final_output_dir}"
            )
        try:
            os.replace(self.staging_dir, self.final_output_dir)
        except OSError as error:
            raise PromotionError(f"could not promote staged output: {error}") from error
        self._promoted = True
        return True
