"""Initial schema for the job search and application tracking workspace.

Revision ID: 0001_initial
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("full_name", sa.String(200)),
        sa.Column("headline", sa.String(300)),
        sa.Column("years_experience", sa.Float()),
        sa.Column("default_titles", JSONB, nullable=False, server_default="[]"),
        sa.Column("default_locations", JSONB, nullable=False, server_default="[]"),
        sa.Column("skills", JSONB, nullable=False, server_default="[]"),
        sa.Column("linkedin_url", sa.String(500)),
        sa.Column("extra", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("normalized_name", sa.String(300), nullable=False),
        sa.Column("domain", sa.String(255)),
        sa.Column("website_url", sa.String(1000)),
        sa.Column("careers_url", sa.String(1000)),
        sa.Column("contacts_checked_at", sa.DateTime(timezone=True)),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("normalized_name", name="uq_company_normalized_name"),
    )
    op.create_index("ix_companies_normalized_name", "companies", ["normalized_name"])
    op.create_index("ix_companies_domain", "companies", ["domain"])

    op.create_table(
        "job_searches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_query", sa.Text(), nullable=False),
        sa.Column("label", sa.String(300)),
        sa.Column("titles", JSONB, nullable=False, server_default="[]"),
        sa.Column("locations", JSONB, nullable=False, server_default="[]"),
        sa.Column("companies", JSONB, nullable=False, server_default="[]"),
        sa.Column("industries", JSONB, nullable=False, server_default="[]"),
        sa.Column("keywords", JSONB, nullable=False, server_default="[]"),
        sa.Column("exclusions", JSONB, nullable=False, server_default="[]"),
        sa.Column("min_years", sa.Float()),
        sa.Column("max_years", sa.Float()),
        sa.Column("experience_level", sa.String(30)),
        sa.Column("remote_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("parse_method", sa.String(30), nullable=False, server_default="rules"),
        sa.Column("parse_notes", JSONB, nullable=False, server_default="[]"),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_job_searches_user_id", "job_searches", ["user_id"])
    op.create_index("ix_search_user_created", "job_searches", ["user_id", "created_at"])

    op.create_table(
        "search_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("search_id", sa.Integer(), sa.ForeignKey("job_searches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="RUNNING"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("providers_queried", JSONB, nullable=False, server_default="[]"),
        sa.Column("providers_skipped", JSONB, nullable=False, server_default="{}"),
        sa.Column("raw_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicates_dropped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("irrelevant_dropped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("validated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unverified", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("contacts_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", JSONB, nullable=False, server_default="[]"),
        sa.Column("notes", JSONB, nullable=False, server_default="[]"),
    )
    op.create_index("ix_search_runs_search_id", "search_runs", ["search_id"])
    op.create_index("ix_run_search_started", "search_runs", ["search_id", "started_at"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("search_id", sa.Integer(), sa.ForeignKey("job_searches.id", ondelete="SET NULL")),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("normalized_title", sa.String(400), nullable=False),
        sa.Column("company_name", sa.String(300), nullable=False),
        sa.Column("location", sa.String(300)),
        sa.Column("normalized_location", sa.String(300)),
        sa.Column("is_remote", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("employment_type", sa.String(80)),
        sa.Column("department", sa.String(200)),
        sa.Column("salary_text", sa.String(200)),
        sa.Column("experience_text", sa.String(300)),
        sa.Column("min_years", sa.Float()),
        sa.Column("max_years", sa.Float()),
        sa.Column("description", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("job_url", sa.String(1000), nullable=False),
        sa.Column("canonical_url", sa.String(1000), nullable=False),
        sa.Column("apply_url", sa.String(1000)),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_query_url", sa.String(1000)),
        sa.Column("external_id", sa.String(200)),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("validation_checks", JSONB, nullable=False, server_default="{}"),
        sa.Column("validation_reason", sa.Text()),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("http_status", sa.Integer()),
        sa.Column("final_url", sa.String(1000)),
        sa.Column("relevance_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("relevance_breakdown", JSONB, nullable=False, server_default="{}"),
        sa.Column("match_reasons", JSONB, nullable=False, server_default="[]"),
        sa.Column("is_saved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_dismissed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "canonical_url", name="uq_job_user_canonical_url"),
    )
    for name, columns in (
        ("ix_jobs_user_id", ["user_id"]),
        ("ix_jobs_company_id", ["company_id"]),
        ("ix_jobs_search_id", ["search_id"]),
        ("ix_jobs_normalized_title", ["normalized_title"]),
        ("ix_jobs_posted_at", ["posted_at"]),
        ("ix_jobs_is_saved", ["is_saved"]),
        ("ix_jobs_is_dismissed", ["is_dismissed"]),
        ("ix_job_fingerprint", ["user_id", "fingerprint"]),
        ("ix_job_validation", ["validation_status"]),
        ("ix_job_discovered", ["discovered_at"]),
        ("ix_job_relevance", ["relevance_score"]),
    ):
        op.create_index(name, "jobs", columns)

    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200)),
        sa.Column("dedupe_key", sa.String(300), nullable=False),
        sa.Column("title", sa.String(250)),
        sa.Column("company_name", sa.String(300), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("profile_url", sa.String(1000)),
        sa.Column("search_url", sa.String(1000)),
        sa.Column("email", sa.String(320)),
        sa.Column("email_status", sa.String(30), nullable=False, server_default="NONE"),
        sa.Column("email_verification", sa.String(20), nullable=False, server_default="not_checked"),
        sa.Column("email_verified_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_url", sa.String(1000)),
        sa.Column("source_excerpt", sa.Text()),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("notes", sa.Text()),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "dedupe_key", name="uq_contact_company_key"),
    )
    op.create_index("ix_contacts_company_id", "contacts", ["company_id"])
    op.create_index("ix_contacts_email", "contacts", ["email"])
    op.create_index("ix_contact_role", "contacts", ["role"])

    op.create_table(
        "job_contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relevance", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("rationale", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "contact_id", name="uq_job_contact"),
    )
    op.create_index("ix_job_contacts_job_id", "job_contacts", ["job_id"])
    op.create_index("ix_job_contacts_contact_id", "job_contacts", ["contact_id"])

    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="SET NULL")),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id", ondelete="SET NULL")),
        sa.Column("job_title", sa.String(400), nullable=False),
        sa.Column("company_name", sa.String(300), nullable=False),
        sa.Column("location", sa.String(300)),
        sa.Column("job_url", sa.String(1000), nullable=False),
        sa.Column("source", sa.String(40)),
        sa.Column("contact_name", sa.String(200)),
        sa.Column("contact_title", sa.String(250)),
        sa.Column("contact_email", sa.String(320)),
        sa.Column("contact_profile_url", sa.String(1000)),
        sa.Column("status", sa.String(30), nullable=False, server_default="SAVED"),
        sa.Column("outreach_status", sa.String(30), nullable=False, server_default="NOT_STARTED"),
        sa.Column("date_found", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("date_saved", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("date_applied", sa.DateTime(timezone=True)),
        sa.Column("outreach_sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_status_change_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("follow_up_date", sa.Date()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "job_id", name="uq_application_user_job"),
    )
    op.create_index("ix_applications_user_id", "applications", ["user_id"])
    op.create_index("ix_applications_job_id", "applications", ["job_id"])
    op.create_index("ix_applications_contact_id", "applications", ["contact_id"])
    op.create_index("ix_application_status", "applications", ["user_id", "status"])
    op.create_index("ix_applications_follow_up_date", "applications", ["follow_up_date"])
    op.create_index("ix_application_follow_up", "applications", ["follow_up_date"])

    op.create_table(
        "application_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_application_events_application_id", "application_events", ["application_id"])


def downgrade() -> None:
    for table in (
        "application_events",
        "applications",
        "job_contacts",
        "contacts",
        "jobs",
        "search_runs",
        "job_searches",
        "companies",
        "user_profile",
        "users",
    ):
        op.drop_table(table)
