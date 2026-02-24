"""Pydantic request/response models for the Nigeria Pharmacy Registry API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VerifyRequest(BaseModel):
    target_level: str = Field(
        ...,
        description="Target validation level (e.g. L1_contact_confirmed)",
    )
    evidence_type: str = Field(
        ...,
        description="Type of evidence (contact_confirmation, location_confirmation, regulator_crossref)",
    )
    capture_method: str | None = Field(
        None,
        description="How evidence was captured (phone_call, site_visit, api_sync, etc.)",
    )
    actor_id: str = Field(
        ...,
        description="ID of the person/system performing verification",
    )
    actor_type: str = Field(
        "field_agent",
        description="Actor type: field_agent, partner_api, regulator_sync, system",
    )
    source_description: str | None = Field(
        None,
        description="Human-readable description of the verification",
    )
    evidence_detail: dict | None = Field(
        None,
        description="Structured evidence metadata",
    )


class TaskGenerateRequest(BaseModel):
    target_level: str = Field(
        ...,
        description="Target validation level (e.g. L1_contact_confirmed)",
    )
    filters: dict | None = Field(
        None,
        description="Optional filters: {state, lga, facility_type}",
    )
    priority: int = Field(
        3,
        ge=1,
        le=5,
        description="Task priority: 1=highest (urgent), 5=lowest (routine)",
    )
    due_date: str | None = Field(
        None,
        description="Due date in YYYY-MM-DD format",
    )
    max_attempts: int = Field(
        3,
        ge=1,
        le=10,
        description="Maximum attempts before task is abandoned",
    )


class TaskSkipRequest(BaseModel):
    reason: str = Field(
        ...,
        description="Reason for skipping the task",
    )
    reschedule: bool = Field(
        False,
        description="If true, create a new task with attempt_count+1",
    )
