from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Segment(Strict):
    segment_id: str
    text: str = Field(min_length=1)
    timestamp: str | None = None
    speaker: str | None = None
    source: str | None = None
    offset: int = 0

class Entry(BaseModel):
    text: str
    timestamp: str | None = None
    source: str | None = None
    speaker: str | None = None
    at: str | None = None

class Meeting(BaseModel):
    id: str | None = Field(default=None, min_length=1, max_length=200)
    title: str | None = None
    participants: list[str] = Field(default_factory=list)
    startedAt: str | None = None

class GenerateRequest(BaseModel):
    meeting: Meeting = Field(default_factory=Meeting)
    transcript: str | list[Entry] | None = None
    transcript_text: str | None = None
    processing: dict | None = None

class Evidence(Strict):
    segment_id: str
    timestamp: str | None = None
    excerpt: str = Field(min_length=1)

class Claim(Strict):
    text: str = Field(min_length=1)
    evidence: list[Evidence] = Field(default_factory=list)
    assessment: Literal["supported", "potentially_unsupported", "ambiguous", "conflicting"] = "ambiguous"

class Topic(Strict):
    topic: str
    summary: Claim
    key_points: list[Claim] = Field(default_factory=list)
    decisions: list[Claim] = Field(default_factory=list)
    unresolved_issues: list[Claim] = Field(default_factory=list)

class Minutes(Strict):
    title: str = "Untitled meeting"
    executive_summary: Claim
    topics: list[Topic] = Field(default_factory=list)
    key_takeaways: list[Claim] = Field(default_factory=list)
    decisions: list[Claim] = Field(default_factory=list)
    conclusions: list[Claim] = Field(default_factory=list)
    unresolved_issues: list[Claim] = Field(default_factory=list)
    follow_up_items: list[Claim] = Field(default_factory=list)
    highlights: list[Claim] = Field(default_factory=list)

class EditRequest(Strict):
    expected_revision: int = Field(ge=1)
    minutes: Minutes
    reviewer: str = Field(min_length=1, max_length=200)

class ApprovalRequest(Strict):
    expected_revision: int = Field(ge=1)
    reviewer: str = Field(min_length=1, max_length=200)
    acknowledge_review_flags: bool = False

class Revision(Strict):
    meeting_id: str
    revision: int
    status: Literal["draft", "approved"]
    created_at: str
    approved_at: str | None = None
    reviewer: str | None = None
    minutes: Minutes
    meeting: Meeting
    segments: list[Segment]
    verification: dict
    generation: dict
    task_records: dict = Field(default_factory=dict)
    storage_mode: str = "mongodb"

class MinutesList(Strict):
    items: list[Revision]
    total: int
    offset: int
    limit: int
