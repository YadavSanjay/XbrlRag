"""Prompt/model regression harness (docs/design/XbrlRag_LLM_Harness.md)."""
from src.eval.regression.prompt_matrix import (
    Arm,
    ArmSummary,
    MatrixResult,
    PromptMatrixRunner,
    RunRecord,
    structural_scorer,
    version_model_matrix,
)

__all__ = [
    "Arm",
    "ArmSummary",
    "MatrixResult",
    "PromptMatrixRunner",
    "RunRecord",
    "structural_scorer",
    "version_model_matrix",
]
