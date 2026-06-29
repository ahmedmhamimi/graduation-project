"""
agents/models.py
Lightweight dataclasses for agent analysis results.
No solver state, no mutations — pure analysis output.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ViolationType(str, Enum):
    # Hard constraint violations
    ROOM_CONFLICT = "room_conflict"
    INSTRUCTOR_CONFLICT = "instructor_conflict"
    CAPACITY_OVERFLOW = "capacity_overflow"
    UNASSIGNED_CLASS = "unassigned_class"
    STUDENT_GROUP_CONFLICT = "student_group_conflict"
    WORKLOAD_EXCEEDED = "workload_exceeded"

    # Soft constraint violations
    ROOM_PREFERENCE = "room_preference"
    TIME_PREFERENCE = "time_preference"
    DISTRIBUTION_ISSUE = "distribution_issue"


class Severity(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SuggestionType(str, Enum):
    REASSIGN_ROOM = "reassign_room"
    CHANGE_TIME = "change_time"
    SPLIT_SECTION = "split_section"
    REASSIGN_INSTRUCTOR = "reassign_instructor"
    ADD_CLASS = "add_class"
    REDUCE_WORKLOAD = "reduce_workload"


@dataclass
class PreValidationWarning:
    """Warning from constraint validation (pre-solve structural issues)."""
    code: str
    message: str
    entity_type: str  # "course", "instructor", "room"
    entity_id: Optional[int] = None
    entity_name: Optional[str] = None
    details: dict = field(default_factory=dict)

    @property
    def severity_class(self) -> str:
        """CSS class for badge coloring."""
        if self.code.startswith("CRIT"):
            return "danger"
        elif self.code.startswith("ERR"):
            return "warning"
        return "info"


@dataclass
class Violation:
    """A constraint violation detected in the timetable."""
    id: str
    type: ViolationType
    severity: Severity
    description: str

    # Affected entities
    affected_class_ids: list[int] = field(default_factory=list)
    affected_room_ids: list[int] = field(default_factory=list)
    affected_instructor_ids: list[int] = field(default_factory=list)
    affected_time_slots: list[tuple] = field(default_factory=list)  # [(days, slot), ...]

    # Human-readable details
    affected_class_names: list[str] = field(default_factory=list)
    affected_room_names: list[str] = field(default_factory=list)
    affected_instructor_names: list[str] = field(default_factory=list)
    affected_time_descriptions: list[str] = field(default_factory=list)

    # Correction hint
    hint: str = ""

    @property
    def severity_class(self) -> str:
        """CSS class for badge coloring."""
        return "danger" if self.severity == Severity.HARD else "warning"

    @property
    def type_label(self) -> str:
        """Human-readable type label."""
        labels = {
            ViolationType.ROOM_CONFLICT: "Room Conflict",
            ViolationType.INSTRUCTOR_CONFLICT: "Instructor Conflict",
            ViolationType.CAPACITY_OVERFLOW: "Capacity Overflow",
            ViolationType.UNASSIGNED_CLASS: "Unassigned Class",
            ViolationType.STUDENT_GROUP_CONFLICT: "Student Group Conflict",
            ViolationType.WORKLOAD_EXCEEDED: "Workload Exceeded",
            ViolationType.ROOM_PREFERENCE: "Room Preference",
            ViolationType.TIME_PREFERENCE: "Time Preference",
            ViolationType.DISTRIBUTION_ISSUE: "Distribution Issue",
        }
        return labels.get(self.type, str(self.type.value))


@dataclass
class ClassifiedViolation:
    """Violation with priority and grouping info from classifier."""
    violation: Violation
    priority: Priority
    group: str  # Grouping key for display
    suggested_strategy: str  # Which correction strategy to use
    impact_score: float = 0.0  # Higher = more impactful

    @property
    def priority_class(self) -> str:
        """CSS class for priority badge."""
        return {
            Priority.CRITICAL: "danger",
            Priority.HIGH: "warning",
            Priority.MEDIUM: "info",
            Priority.LOW: "secondary",
        }.get(self.priority, "secondary")

    @property
    def priority_label(self) -> str:
        """Human-readable priority."""
        return self.priority.value.title()


@dataclass
class Suggestion:
    """A suggested correction action (never applied automatically)."""
    id: str
    type: SuggestionType
    description: str
    rationale: str

    # What this suggestion targets
    target_class_id: Optional[int] = None
    target_class_name: Optional[str] = None

    # The proposed change
    proposed_room_id: Optional[int] = None
    proposed_room_name: Optional[str] = None
    proposed_days: Optional[int] = None
    proposed_slot: Optional[int] = None
    proposed_time_description: Optional[str] = None
    proposed_instructor_id: Optional[int] = None
    proposed_instructor_name: Optional[str] = None

    # Link to edit page
    edit_link: Optional[str] = None

    # Which violation this addresses
    addresses_violation_id: Optional[str] = None

    # Confidence/feasibility (0.0 - 1.0)
    confidence: float = 1.0

    @property
    def type_label(self) -> str:
        """Human-readable type."""
        labels = {
            SuggestionType.REASSIGN_ROOM: "Reassign Room",
            SuggestionType.CHANGE_TIME: "Change Time",
            SuggestionType.SPLIT_SECTION: "Split Section",
            SuggestionType.REASSIGN_INSTRUCTOR: "Reassign Instructor",
            SuggestionType.ADD_CLASS: "Add Assignment",
            SuggestionType.REDUCE_WORKLOAD: "Reduce Workload",
        }
        return labels.get(self.type, str(self.type.value))

    @property
    def type_class(self) -> str:
        """CSS class for type badge."""
        return {
            SuggestionType.REASSIGN_ROOM: "primary",
            SuggestionType.CHANGE_TIME: "info",
            SuggestionType.SPLIT_SECTION: "warning",
            SuggestionType.REASSIGN_INSTRUCTOR: "secondary",
            SuggestionType.ADD_CLASS: "success",
            SuggestionType.REDUCE_WORKLOAD: "danger",
        }.get(self.type, "secondary")

    @property
    def confidence_percent(self) -> int:
        """Confidence as percentage."""
        return int(self.confidence * 100)


@dataclass
class ConstraintValidationReport:
    """Output from constraint validation agent (pre-solve checks)."""
    timestamp: datetime
    session_id: int
    warnings: list[PreValidationWarning] = field(default_factory=list)

    # Summary counts
    total_courses: int = 0
    total_rooms: int = 0
    total_instructors: int = 0
    total_classes: int = 0

    # Status
    has_critical_issues: bool = False

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def is_clean(self) -> bool:
        return len(self.warnings) == 0

    @property
    def critical_count(self) -> int:
        return len([w for w in self.warnings if w.code.startswith("CRIT")])

    @property
    def error_count(self) -> int:
        return len([w for w in self.warnings if w.code.startswith("ERR")])

    @property
    def warn_count(self) -> int:
        return len([w for w in self.warnings if w.code.startswith("WARN")])


@dataclass
class TimetableValidationReport:
    """Output from timetable validation agent (post-solve checks)."""
    timestamp: datetime
    session_id: int
    violations: list[Violation] = field(default_factory=list)

    # Source info
    xml_file: Optional[str] = None
    xml_loaded: bool = False
    total_assignments: int = 0

    @property
    def hard_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == Severity.HARD]

    @property
    def soft_violations(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == Severity.SOFT]

    @property
    def hard_count(self) -> int:
        return len(self.hard_violations)

    @property
    def soft_count(self) -> int:
        return len(self.soft_violations)

    @property
    def is_feasible(self) -> bool:
        return self.hard_count == 0

    @property
    def violations_by_type(self) -> dict:
        """Group violations by type for display."""
        result = {}
        for v in self.violations:
            key = v.type.value
            if key not in result:
                result[key] = []
            result[key].append(v)
        return result


@dataclass
class ClassificationReport:
    """Output from violation classification agent."""
    timestamp: datetime
    session_id: int
    classified: list[ClassifiedViolation] = field(default_factory=list)

    # Grouped by type
    by_type: dict = field(default_factory=dict)
    by_priority: dict = field(default_factory=dict)

    @property
    def critical_count(self) -> int:
        return len([c for c in self.classified if c.priority == Priority.CRITICAL])

    @property
    def high_count(self) -> int:
        return len([c for c in self.classified if c.priority == Priority.HIGH])

    @property
    def medium_count(self) -> int:
        return len([c for c in self.classified if c.priority == Priority.MEDIUM])

    @property
    def low_count(self) -> int:
        return len([c for c in self.classified if c.priority == Priority.LOW])

    @property
    def total_count(self) -> int:
        return len(self.classified)

    @property
    def grouped_by_group(self) -> dict:
        """Group classified violations by their group label."""
        result = {}
        for cv in self.classified:
            if cv.group not in result:
                result[cv.group] = []
            result[cv.group].append(cv)
        return result


@dataclass
class SuggestionReport:
    """Output from correction suggester agent."""
    timestamp: datetime
    session_id: int
    suggestions: list[Suggestion] = field(default_factory=list)

    # Grouped by type
    by_type: dict = field(default_factory=dict)

    @property
    def total_suggestions(self) -> int:
        return len(self.suggestions)

    @property
    def high_confidence(self) -> list[Suggestion]:
        """Suggestions with confidence >= 0.8."""
        return [s for s in self.suggestions if s.confidence >= 0.8]

    @property
    def grouped_by_violation(self) -> dict:
        """Group suggestions by the violation they address."""
        result = {}
        for s in self.suggestions:
            key = s.addresses_violation_id or "general"
            if key not in result:
                result[key] = []
            result[key].append(s)
        return result


@dataclass
class FullAnalysisReport:
    """Combined report from all agents."""
    timestamp: datetime
    session_id: int

    constraint_report: ConstraintValidationReport
    validation_report: TimetableValidationReport
    classification_report: ClassificationReport
    suggestion_report: SuggestionReport

    @property
    def is_healthy(self) -> bool:
        return (
            self.constraint_report.is_clean and
            self.validation_report.is_feasible
        )

    @property
    def total_issues(self) -> int:
        return (
            self.constraint_report.warning_count +
            self.validation_report.hard_count +
            self.validation_report.soft_count
        )

    @property
    def status_class(self) -> str:
        """CSS class for overall status."""
        if self.is_healthy:
            return "success"
        elif self.validation_report.hard_count > 0:
            return "danger"
        elif self.constraint_report.has_critical_issues:
            return "danger"
        return "warning"

    @property
    def status_label(self) -> str:
        """Human-readable status."""
        if self.is_healthy:
            return "Schedule is Healthy"
        elif self.validation_report.hard_count > 0:
            return f"{self.validation_report.hard_count} Hard Violation(s)"
        return f"{self.total_issues} Issue(s) Found"
