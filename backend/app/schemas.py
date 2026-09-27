from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TeacherProfileUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=50)
    subject: str = Field(min_length=1, max_length=50)
    years_experience: int = Field(ge=0, le=80)
    teaching_style: str = Field(default="", max_length=2000)


class PrecisionTeachingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=2000)
    content: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(default="", max_length=3000)
    subject: str = Field(default="数学", max_length=50)
    grade: str = Field(default="高一", max_length=50)
    textbook: str = Field(default="", max_length=200)
    estimated_periods: int = Field(default=1, ge=1, le=100)


class CurrentTeachingUpdate(BaseModel):
    teaching_id: str = Field(min_length=1, max_length=100)


class ClassroomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    student_count: int = Field(default=0, ge=0, le=1000)
    background: str = Field(default="", max_length=2000)


class ClassroomUpdate(BaseModel):
    student_count: int = Field(ge=0, le=1000)
    background: str = Field(default="", max_length=2000)


class DiagnosisTaskUpdate(BaseModel):
    diagnosis_type: Literal["pre", "during", "post"] = "pre"
    goal: str = Field(default="", max_length=3000)
    task_text: str = Field(default="", max_length=8000)
    ai_role: str = Field(default="", max_length=3000)
    duration_minutes: int = Field(default=15, ge=1, le=180)
    status: Literal["draft", "published"] = "draft"
    classroom_ids: list[str] | None = None


class InterventionPlanUpdate(BaseModel):
    duration_minutes: int = Field(default=45, ge=10, le=300)
    evidence_summary: str = Field(default="", max_length=8000)
    teacher_judgment: str = Field(default="", max_length=8000)
    status: Literal["draft", "completed"] = "draft"


class AIJobCreate(BaseModel):
    skill_key: Literal["expression_polish", "intervention_design", "solo_task_rubric_builder", "solo_student_diagnosis_feedback"]
    input: dict[str, Any]


class DiagnosisRubricUpdate(BaseModel):
    rubric: dict[str, Any]
    confirmed: bool = False


class AIJobResponse(BaseModel):
    id: str
    skill_key: str
    skill_version: str
    provider: str
    status: str
    output: dict[str, Any] | None = None
    error: str | None = None


class StudentLogin(BaseModel):
    school_name: str = Field(min_length=1, max_length=120)
    class_name: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=50)


class StudentMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class StudentTaskSubmit(BaseModel):
    submitted_text: str = Field(default="", max_length=12000)


class StudentDiagnosisReportUpdate(BaseModel):
    report_text: str = Field(min_length=1, max_length=20000)
    status: Literal["draft", "confirmed", "pushed"] = "draft"
