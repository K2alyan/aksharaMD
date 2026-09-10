"""Export the independent quality-gate result beside compatible outputs."""
from __future__ import annotations

from ...assessment.compiler_binding import save_compiled_assessment
from ...context import CompilationContext
from ..base import ExporterPlugin
from ..registry import register_plugin


class QualityAssessmentExporter(ExporterPlugin):
    """Persist assessment evidence after ``document.md`` without enforcement."""

    name = "quality_assessment_exporter"
    priority = 92

    def execute(self, ctx: CompilationContext) -> CompilationContext:
        save_compiled_assessment(ctx)
        return ctx


register_plugin(QualityAssessmentExporter)
