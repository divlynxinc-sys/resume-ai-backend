from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl, EmailStr


class ResumeSection(str, Enum):
    info = "info"
    experience = "experience"
    education = "education"
    skills = "skills"
    summary = "summary"
    job_description = "job_description"
    custom = "custom"


class PersonalInfo(BaseModel):
    full_name: str = ""
    email: EmailStr | None = None
    phone: str = ""
    location: Optional[str] = None
    linkedin_url: Optional[str] = None
    portfolio_url: Optional[str] = None


class ExperienceItem(BaseModel):
    role: Optional[str] = None
    company: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None


class EducationItem(BaseModel):
    school: str = ""
    degree: str = ""
    start_date: str = ""
    end_date: str = ""
    location: Optional[str] = None
    field_of_study: Optional[str] = None


class JobDescription(BaseModel):
    job_title: str = ""
    company: str = ""
    location: Optional[str] = None
    description: str = ""


class ResumeContent(BaseModel):
    info: PersonalInfo = Field(default_factory=PersonalInfo)
    experience: List[ExperienceItem] = Field(default_factory=list)
    education: List[EducationItem] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    summary: str = ""
    job_description: JobDescription = Field(default_factory=JobDescription)
    custom: dict = Field(default_factory=dict)


class ResumeCreate(BaseModel):
    title: str
    template_id: Optional[int] = None
    content: Optional[ResumeContent] = None


class ResumeUpdate(BaseModel):
    title: Optional[str] = None
    template_id: Optional[int] = None
    status: Optional[str] = None
    content: Optional[ResumeContent] = None


class ResumeItem(BaseModel):
    id: int
    title: str
    updated_at: datetime
    status: str


class ResumeDetail(BaseModel):
    id: int
    title: str
    template_id: Optional[int] = None
    status: str
    content: Optional[ResumeContent] = None
    created_at: datetime
    updated_at: datetime


class SectionUpdate(BaseModel):
    # Accept arbitrary shape per section
    data: dict | list | str | None = None

