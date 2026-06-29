"""
agents/violation_classifier.py
Agent 3 — Violation Classification Agent

Groups violations by type and assigns priorities.
Determines which correction strategy is most appropriate.
"""

from datetime import datetime
from collections import defaultdict

from .models import (
    Violation, ViolationType, Severity, Priority,
    ClassifiedViolation, ClassificationReport
)


# Priority mapping based on violation type and severity
PRIORITY_MAP = {
    (ViolationType.ROOM_CONFLICT, Severity.HARD): Priority.CRITICAL,
    (ViolationType.INSTRUCTOR_CONFLICT, Severity.HARD): Priority.CRITICAL,
    (ViolationType.CAPACITY_OVERFLOW, Severity.HARD): Priority.HIGH,
    (ViolationType.UNASSIGNED_CLASS, Severity.HARD): Priority.HIGH,
    (ViolationType.STUDENT_GROUP_CONFLICT, Severity.HARD): Priority.HIGH,
    (ViolationType.WORKLOAD_EXCEEDED, Severity.HARD): Priority.MEDIUM,
    (ViolationType.ROOM_PREFERENCE, Severity.SOFT): Priority.LOW,
    (ViolationType.TIME_PREFERENCE, Severity.SOFT): Priority.LOW,
    (ViolationType.DISTRIBUTION_ISSUE, Severity.SOFT): Priority.LOW,
}

# Suggested correction strategy per violation type
STRATEGY_MAP = {
    ViolationType.ROOM_CONFLICT: "reassign_room_or_time",
    ViolationType.INSTRUCTOR_CONFLICT: "change_time_or_instructor",
    ViolationType.CAPACITY_OVERFLOW: "reassign_room_or_split",
    ViolationType.UNASSIGNED_CLASS: "find_available_slot",
    ViolationType.STUDENT_GROUP_CONFLICT: "change_time",
    ViolationType.WORKLOAD_EXCEEDED: "reassign_instructor",
    ViolationType.ROOM_PREFERENCE: "consider_room_change",
    ViolationType.TIME_PREFERENCE: "consider_time_change",
    ViolationType.DISTRIBUTION_ISSUE: "adjust_distribution",
}

# Group labels for display
GROUP_LABELS = {
    ViolationType.ROOM_CONFLICT: "Room Conflicts",
    ViolationType.INSTRUCTOR_CONFLICT: "Instructor Conflicts",
    ViolationType.CAPACITY_OVERFLOW: "Capacity Issues",
    ViolationType.UNASSIGNED_CLASS: "Unassigned Classes",
    ViolationType.STUDENT_GROUP_CONFLICT: "Student Conflicts",
    ViolationType.WORKLOAD_EXCEEDED: "Workload Issues",
    ViolationType.ROOM_PREFERENCE: "Room Preferences",
    ViolationType.TIME_PREFERENCE: "Time Preferences",
    ViolationType.DISTRIBUTION_ISSUE: "Distribution Issues",
}

# Impact score multipliers
IMPACT_MULTIPLIERS = {
    Priority.CRITICAL: 10.0,
    Priority.HIGH: 5.0,
    Priority.MEDIUM: 2.0,
    Priority.LOW: 1.0,
}


class ViolationClassifier:
    """
    Classifies and prioritizes violations for display and correction.
    """

    def classify(
        self,
        violations: list[Violation],
        session_id: int,
    ) -> ClassificationReport:
        """
        Classify all violations with priorities and groupings.

        Args:
            violations: List of Violation objects from TimetableValidator
            session_id: Current session ID

        Returns:
            ClassificationReport with classified violations
        """
        classified = []
        by_type = defaultdict(list)
        by_priority = defaultdict(list)

        for violation in violations:
            cv = self._classify_violation(violation)
            classified.append(cv)
            by_type[violation.type.value].append(cv)
            by_priority[cv.priority.value].append(cv)

        # Sort by priority (critical first) then by impact score
        priority_order = {
            Priority.CRITICAL: 0,
            Priority.HIGH: 1,
            Priority.MEDIUM: 2,
            Priority.LOW: 3,
        }
        classified.sort(key=lambda c: (priority_order[c.priority], -c.impact_score))

        return ClassificationReport(
            timestamp=datetime.now(),
            session_id=session_id,
            classified=classified,
            by_type=dict(by_type),
            by_priority=dict(by_priority),
        )

    def _classify_violation(self, violation: Violation) -> ClassifiedViolation:
        """Classify a single violation."""
        # Determine priority
        priority = PRIORITY_MAP.get(
            (violation.type, violation.severity),
            Priority.MEDIUM
        )

        # Determine suggested strategy
        strategy = STRATEGY_MAP.get(
            violation.type,
            "manual_review"
        )

        # Calculate impact score based on affected entities
        impact = self._calculate_impact(violation, priority)

        # Create group key for display
        group = GROUP_LABELS.get(violation.type, "Other Issues")

        return ClassifiedViolation(
            violation=violation,
            priority=priority,
            group=group,
            suggested_strategy=strategy,
            impact_score=impact,
        )

    def _calculate_impact(
        self,
        violation: Violation,
        priority: Priority
    ) -> float:
        """Calculate impact score based on number of affected entities."""
        base_multiplier = IMPACT_MULTIPLIERS.get(priority, 1.0)

        # More affected entities = higher impact
        entity_count = (
            len(violation.affected_class_ids) +
            len(violation.affected_room_ids) * 0.5 +
            len(violation.affected_instructor_ids) * 0.5
        )

        return base_multiplier * max(1.0, entity_count)
