# models.py

from pydantic import BaseModel, Field
from typing import List, Optional, Literal
class ExplicitTask(BaseModel):
    task_id: str = Field(description="Unique identifier for the task, e.g., T1")
    description: str = Field(description="Exact description of the task based on the transcript")
    explicit_owner: Optional[str] = Field(
        default=None, 
        description="The person explicitly assigned to this task. Leave null if not stated out loud."
    )
    explicit_deadline: Optional[str] = Field(
        default=None, 
        description="The exact deadline stated. Leave null if not explicitly stated."
    )
    explicit_prerequisites: List[str] = Field(
        default_factory=list, 
        description="List of task_ids that must be completed before this one, only if explicitly stated."
    )

class FactualExtraction(BaseModel):
    meeting_title: str
    date: str
    tasks: List[ExplicitTask]

class EnrichedTask(BaseModel):
    task_id: str = Field(description="Must exactly match the task_id from Pass 1")
    description: str = Field(description="Task description")
    owner: Optional[str] = Field(description="The explicitly stated owner, or logically inferred owner. Null if entirely unknown.")
    missing_owner: bool = Field(description="Set to True if the owner was not explicitly stated and cannot be inferred confidently.")
    deadline: Optional[str] = Field(description="Explicitly stated or logically inferred deadline.")
    missing_deadline: bool = Field(description="Set to True if no deadline was stated or implied.")
    priority: Literal["Low", "Medium", "High"] = Field(description="Infer priority based on urgency words, deadlines, and project context.")
    explicit_prerequisites: List[str] = Field(
            description="List of task IDs (e.g., 'T1') OR string descriptions of external blockers (e.g., 'Waiting on AWS support') explicitly stated as prerequisites."
        )
    inferred_prerequisites: List[str] = Field(
        default_factory=list, 
        description="Task IDs OR string descriptions of external blockers that logically must happen first, even if not explicitly stated."
    )
    status: Literal["pending", "in progress", "completed", "blocked"] = Field(
        default="pending", 
        description="Inferred current status of the task based on the meeting dialogue."
    )

class ContextualInference(BaseModel):
    enriched_tasks: List[EnrichedTask]