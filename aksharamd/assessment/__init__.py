"""Parser-independent, source-grounded document assessment."""

from .models import (
    DEFAULT_ASSESSMENT_POLICY_ID,
    AssessmentDisposition,
    AssessmentResult,
    CandidateArtifact,
    EvidenceStatus,
    RequiredLiteralRelationship,
    SourceArtifact,
    TaskProfile,
    Verdict,
)
from .service import Assessor
from .source_candidate import (
    SOURCE_CANDIDATE_POLICY_ID,
    DetectorResult,
    DetectorStatus,
    EvidenceScope,
    SourceCandidateAssessment,
    assess_source_candidate,
)
from .staging import PromotionError, StagedOutput

__all__ = [
    "DEFAULT_ASSESSMENT_POLICY_ID", "AssessmentDisposition", "AssessmentResult", "Assessor", "CandidateArtifact",
    "EvidenceStatus", "PromotionError", "RequiredLiteralRelationship", "SourceArtifact", "StagedOutput",
    "TaskProfile", "Verdict",
    "SOURCE_CANDIDATE_POLICY_ID", "DetectorResult", "DetectorStatus", "EvidenceScope",
    "SourceCandidateAssessment", "assess_source_candidate",
]
