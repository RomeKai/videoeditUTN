from pydantic import BaseModel, Field
from typing import List, Optional, Dict

class TechnicalSpecification(BaseModel):
    """
    Blueprint defined by the Lead Architect before implementation.
    Acts as a 'Technical Contract' for the Engineering team.
    """
    requirement_id: str = Field(description="Unique identifier for the requirement")
    affected_files: List[str] = Field(description="List of files to be created or modified")
    implementation_plan: List[str] = Field(description="Step-by-step logic and architecture design")
    database_changes: List[str] = Field(description="Description of changes to Django models or migrations")
    security_risk_assessment: str = Field(description="Analysis of potential security or performance risks")

class QAReport(BaseModel):
    """
    Audit report for the Quality Assurance engineer to approve or reject a task.
    Ensures engineering excellence and financial/system stability.
    """
    is_approved: bool = Field(description="True if the code is ready for production")
    code_quality_score: float = Field(ge=0.0, le=10.0, description="Overall score from 1 to 10")
    financial_integrity_check: bool = Field(description="Verifies that transaction rollbacks are properly handled")
    performance_check: bool = Field(description="Verifies CPU/Memory optimization standards")
    final_source_code: str = Field(description="The definitive and corrected code approved by QA")
    audit_notes: str = Field(description="Technical feedback and justification for the decision")

# --- Original Contracts (Kept for compatibility) ---

class ArchitectureContract(BaseModel):
    """
    Blueprint defined by the Lead Architect before implementation.
    Acts as a 'Jira Ticket' for the Engineering team.
    """
    ticket_id: str
    proposed_files: List[str] = Field(description="List of files to be created or modified")
    external_libraries: List[str] = Field(description="New dependencies to be added to requirements")
    db_impact: Dict[str, str] = Field(
        description="Description of changes to PostgreSQL models (fields, tables, migrations)"
    )
    design_patterns: List[str] = Field(
        description="GoF or GRASP patterns to be applied (e.g., Strategy, Factory)"
    )
    risk_assessment: str = Field(description="Potential breaking changes or side effects")

class QAApprovalContract(BaseModel):
    """
    Checklist for the Auditor to approve or reject a task.
    Ensures engineering excellence and video processing stability.
    """
    task_id: str
    passed_visual_verification: bool = False
    exception_handling_check: bool = Field(
        default=False, description="Are all MoviePy/FFmpeg exceptions caught?"
    )
    cpu_memory_optimization: bool = Field(
        default=False, description="Is the code optimized for multi-core processing?"
    )
    frame_skipping_check: bool = Field(
        default=False, description="Is the output framerate consistent and verified?"
    )
    final_score: float = Field(ge=0.0, le=10.0, description="Overall code quality score")
    audit_comments: str

class AgentTask(BaseModel):
    """Schema for general tasks assigned to engineering agents."""
    id: str
    title: str
    description: str
    priority: int = Field(default=1, ge=1, le=5)
    target_files: List[str]
    context_required: bool = True

class EngineeringReport(BaseModel):
    """Final output from a Developer agent after finishing a task."""
    task_id: str
    status: str # SUCCESS, FAILED, PARTIAL
    changes_made: List[str]
    technical_notes: Optional[str]
    suggestions: List[str]
