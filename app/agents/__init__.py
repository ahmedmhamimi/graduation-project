"""
agents/__init__.py
Multi-Agent System for timetable analysis.
Suggestion-only — no automatic corrections.
"""

from .models import (
    ViolationType, Severity, Priority, SuggestionType,
    PreValidationWarning, Violation, ClassifiedViolation, Suggestion,
    ConstraintValidationReport, TimetableValidationReport,
    ClassificationReport, SuggestionReport, FullAnalysisReport,
)
from .constraint_validator import ConstraintValidator
from .timetable_validator import TimetableValidator
from .violation_classifier import ViolationClassifier
from .correction_suggester import CorrectionSuggester

__all__ = [
    # Enums
    "ViolationType", "Severity", "Priority", "SuggestionType",
    # Data models
    "PreValidationWarning", "Violation", "ClassifiedViolation", "Suggestion",
    # Reports
    "ConstraintValidationReport", "TimetableValidationReport",
    "ClassificationReport", "SuggestionReport", "FullAnalysisReport",
    # Agents
    "ConstraintValidator", "TimetableValidator",
    "ViolationClassifier", "CorrectionSuggester",
]
