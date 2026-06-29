from app.models.user import UserContext
from app.models.schedule import CourseBlock

class StaffViewModel:
    def __init__(self, role: str):
        self.role = role

    def get_context(self) -> UserContext:
        name = "Teaching Assistant" if self.role == "ta" else "Dr. Lecturer"
        return UserContext(id="staff1", name=name, role_id=self.role, email="staff@uni.edu")

    def get_related_staff(self):
        # If lecturer, return TAs. If TA, return Lecturers.
        if self.role == 'lecturer':
            return [
                {"course": "Algorithms (CS202)", "name": "TA. Youssef Ibrahim"},
                {"course": "Deep Learning (CS450)", "name": "TA. Ahmed Khaled"}
            ]
        else:
            return [
                {"course": "Algorithms (CS202)", "name": "Dr. Omar Farid"},
                {"course": "Deep Learning (CS450)", "name": "Dr. Karim Adel"}
            ]