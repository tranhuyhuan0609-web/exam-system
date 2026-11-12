"""Re-export UniversalExamAndMaterialIngestor for app.ai namespace."""

from universal_ingestor import (
    UniversalExamAndMaterialIngestor,
    extract_short_answer_value,
    find_soffice_executable,
)

__all__ = [
    "UniversalExamAndMaterialIngestor",
    "extract_short_answer_value",
    "find_soffice_executable",
]
