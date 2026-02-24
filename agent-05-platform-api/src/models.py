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


class ReverificationGenerateRequest(BaseModel):
    target_level: str | None = Field(
        None,
        description="Level to check for reverification (e.g. L1_contact_confirmed). Omit to scan all levels.",
    )
    include_expiring_soon: bool = Field(
        False,
        description="Include pharmacies expiring within the grace period",
    )


class DowngradeRequest(BaseModel):
    reason: str = Field(
        ...,
        description="Reason for the downgrade",
    )
    actor_id: str = Field(
        "system",
        description="Actor performing the downgrade",
    )


class RegulatorUploadRequest(BaseModel):
    regulator_source: str = Field(
        ...,
        description="Regulator source: 'pcn', 'nhia', or 'nafdac'",
    )
    extract_date: str | None = Field(
        None,
        description="Date when the regulator generated the data (YYYY-MM-DD)",
    )
    max_records: int = Field(
        5000,
        ge=1,
        le=25000,
        description="Maximum records to process in this batch",
    )


class RegulatorReviewRequest(BaseModel):
    action: str = Field(
        ...,
        description="Review action: 'approve' or 'reject'",
    )
    matched_pharmacy_id: str | None = Field(
        None,
        description="Override pharmacy ID for manual matching (optional)",
    )
    notes: str | None = Field(
        None,
        description="Reviewer notes",
    )


class RegulatorBatchApproveRequest(BaseModel):
    dry_run: bool = Field(
        False,
        description="If true, calculate what would happen without executing promotions",
    )


# ---------------------------------------------------------------------------
# SMS Campaign models
# ---------------------------------------------------------------------------


class SmsCampaignCreateRequest(BaseModel):
    campaign_name: str = Field(
        ...,
        description="Human-readable campaign name",
        max_length=255,
    )
    description: str | None = Field(
        None,
        description="Campaign description / purpose",
    )
    message_template: str | None = Field(
        None,
        description="SMS template with {pharmacy_name}, {address}, {msg_id_short} placeholders. "
        "Uses default if not provided.",
    )
    max_attempts: int = Field(
        3,
        ge=1,
        le=5,
        description="Maximum send attempts per pharmacy",
    )
    retry_interval_hours: int = Field(
        48,
        ge=12,
        le=168,
        description="Hours between retry attempts",
    )
    filters: dict | None = Field(
        None,
        description="Target filters: {state, lga, facility_type}",
    )


class SmsDeliveryWebhook(BaseModel):
    provider_message_id: str = Field(
        ...,
        description="SMS provider's message ID",
    )
    status: str = Field(
        ...,
        description="Delivery status from provider: 'delivered', 'failed', 'rejected'",
    )
    failure_reason: str | None = Field(
        None,
        description="Reason for delivery failure (if applicable)",
    )
    timestamp: str | None = Field(
        None,
        description="ISO 8601 timestamp from provider",
    )


class SmsReplyWebhook(BaseModel):
    from_number: str = Field(
        ...,
        description="Phone number that sent the reply (E.164 format)",
    )
    message_text: str = Field(
        ...,
        description="Raw text of the inbound SMS",
    )
    provider_message_id: str | None = Field(
        None,
        description="Provider's ID for this inbound message",
    )
    timestamp: str | None = Field(
        None,
        description="ISO 8601 timestamp of when the reply was received",
    )
