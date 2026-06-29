"""
services/agent_service.py
Orchestrates the agent pipeline and bridges to UniTime DB + XML.
"""

from datetime import datetime
from typing import Optional

from app.agents import (
    ConstraintValidator, TimetableValidator,
    ViolationClassifier, CorrectionSuggester,
    FullAnalysisReport, ConstraintValidationReport,
    TimetableValidationReport, ClassificationReport,
    SuggestionReport,
)
from app.services.unitime_db import UniTimeDB
from app.services.xml_schedule import XMLSchedule


class AgentService:
    """
    Orchestrates the multi-agent analysis pipeline.
    
    Flow:
    1. Fetch data from DB
    2. Load solution from XML
    3. Run constraint validator (pre-solve checks)
    4. Run timetable validator (post-solve checks against XML)
    5. Run violation classifier (prioritize)
    6. Run correction suggester (generate suggestions)
    7. Return combined report
    """
    
    def __init__(self, db: UniTimeDB, xml_schedule: Optional[XMLSchedule] = None):
        self.db = db
        self.xml_schedule = xml_schedule
        self.constraint_validator = ConstraintValidator()
        self.timetable_validator = TimetableValidator()
        self.violation_classifier = ViolationClassifier()
        self.correction_suggester = CorrectionSuggester()
    
    async def run_full_analysis(
        self,
        session_id: Optional[int] = None,
    ) -> FullAnalysisReport:
        """
        Run the complete analysis pipeline.
        
        Args:
            session_id: Session ID to analyze. If None, uses default.
            
        Returns:
            FullAnalysisReport containing all agent outputs
        """
        sid = session_id or self.db.session_id
        
        # 1. Fetch data from DB
        courses = await self.db.get_courses(sid)
        rooms = await self.db.get_rooms(sid)
        instructors = await self.db.get_instructors(session_id=sid)
        classes = await self.db.get_classes(sid)
        
        # 2. Ensure XML is loaded
        xml_schedule = self.xml_schedule
        if xml_schedule and not xml_schedule.is_loaded():
            xml_schedule.load()
        
        # 3. Run Constraint Validator (Agent 1)
        constraint_report = self.constraint_validator.validate(
            courses=courses,
            rooms=rooms,
            instructors=instructors,
            classes=classes,
            session_id=sid,
        )
        
        # 4. Run Timetable Validator (Agent 2)
        validation_report = self.timetable_validator.validate(
            classes=classes,
            rooms=rooms,
            instructors=instructors,
            xml_schedule=xml_schedule,
            session_id=sid,
        )
        
        # 5. Run Violation Classifier (Agent 3)
        classification_report = self.violation_classifier.classify(
            violations=validation_report.violations,
            session_id=sid,
        )
        
        # 6. Run Correction Suggester (Agent 4)
        suggestion_report = self.correction_suggester.suggest(
            classified_violations=classification_report.classified,
            classes=classes,
            rooms=rooms,
            instructors=instructors,
            xml_schedule=xml_schedule,
            session_id=sid,
        )
        
        return FullAnalysisReport(
            timestamp=datetime.now(),
            session_id=sid,
            constraint_report=constraint_report,
            validation_report=validation_report,
            classification_report=classification_report,
            suggestion_report=suggestion_report,
        )
    
    async def run_constraint_validation_only(
        self,
        session_id: Optional[int] = None,
    ) -> ConstraintValidationReport:
        """Run only the constraint validation agent."""
        sid = session_id or self.db.session_id
        
        courses = await self.db.get_courses(sid)
        rooms = await self.db.get_rooms(sid)
        instructors = await self.db.get_instructors(session_id=sid)
        classes = await self.db.get_classes(sid)
        
        return self.constraint_validator.validate(
            courses=courses,
            rooms=rooms,
            instructors=instructors,
            classes=classes,
            session_id=sid,
        )
    
    async def run_timetable_validation_only(
        self,
        session_id: Optional[int] = None,
    ) -> TimetableValidationReport:
        """Run only the timetable validation agent."""
        sid = session_id or self.db.session_id
        
        classes = await self.db.get_classes(sid)
        rooms = await self.db.get_rooms(sid)
        instructors = await self.db.get_instructors(session_id=sid)
        
        xml_schedule = self.xml_schedule
        if xml_schedule and not xml_schedule.is_loaded():
            xml_schedule.load()
        
        return self.timetable_validator.validate(
            classes=classes,
            rooms=rooms,
            instructors=instructors,
            xml_schedule=xml_schedule,
            session_id=sid,
        )