"""
migrations/versions/0001_init.py

Initial database schema — Phase 1 of PLAN_WEB_SAAS.md.

Creates:
  - pgvector + uuid-ossp extensions
  - organizations, users tables (tenant hierarchy)
  - meetings, mom_records, mom_edit_history tables (core business data)
  - meeting_embeddings table with VECTOR(3072) column (RAG memory store)
  - Row-Level Security policies on meetings, mom_records, meeting_embeddings

The RLS policies use the `app.current_org` Postgres session variable, which
is set per-request by core/db_session.py's get_tenant_session(). Even if an
application-layer WHERE clause is accidentally omitted in some future query,
the database will still refuse cross-tenant reads/writes.
"""

from alembic import op


# Alembic revision identifiers
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extensions ────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

    # ── Tenancy ───────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE organizations (
            id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name        TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)

    op.execute("""
        CREATE TABLE users (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            email           TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            role            TEXT NOT NULL DEFAULT 'member',
            whatsapp_number TEXT,
            notify_email    TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)

    # ── Meetings & MOMs ───────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE meetings (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            created_by      UUID NOT NULL REFERENCES users(id),
            title           TEXT,
            meeting_context TEXT NOT NULL,
            raw_transcript  TEXT,
            status          TEXT NOT NULL DEFAULT 'recording',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at    TIMESTAMPTZ
        );
    """)
    op.execute("CREATE INDEX idx_meetings_org ON meetings(org_id);")

    op.execute("""
        CREATE TABLE mom_records (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            meeting_id      UUID UNIQUE NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
            summary         TEXT,
            key_decisions   JSONB NOT NULL DEFAULT '[]',
            action_items    JSONB NOT NULL DEFAULT '[]',
            risks           JSONB NOT NULL DEFAULT '[]',
            next_steps      TEXT,
            version         INT NOT NULL DEFAULT 1,
            last_edited_by  UUID REFERENCES users(id),
            last_edited_at  TIMESTAMPTZ
        );
    """)

    # Every manual edit is captured here — raw material for correction embeddings
    # that close the learning loop (Phase 5 of the plan).
    op.execute("""
        CREATE TABLE mom_edit_history (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            mom_record_id   UUID NOT NULL REFERENCES mom_records(id) ON DELETE CASCADE,
            org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            field_path      TEXT NOT NULL,
            old_value       TEXT,
            new_value       TEXT,
            edited_by       UUID NOT NULL REFERENCES users(id),
            edited_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)

    # ── The Memory: meeting + correction embeddings ────────────────────────
    op.execute("""
        CREATE TABLE meeting_embeddings (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            meeting_id      UUID REFERENCES meetings(id) ON DELETE CASCADE,
            content_type    TEXT NOT NULL,
            source_text     TEXT NOT NULL,
            embedding       VECTOR(3072) NOT NULL,
            metadata        JSONB NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """)

    # Plain B-tree index for org-scoped lookups (WHERE org_id = ...)
    op.execute("CREATE INDEX idx_embeddings_org ON meeting_embeddings(org_id);")

    # No HNSW index: pgvector's HNSW index has a 2000-dimension ceiling, while
    # Gemini embedding-001 returns 3072-dimensional vectors in this codebase.

    # ── Defense-in-depth: Postgres Row-Level Security ─────────────────────
    # The app sets `app.current_org` per-request (see core/db_session.py).
    # Even if an application-layer WHERE clause is forgotten in some future
    # query, RLS prevents cross-tenant reads/writes at the database layer.
    op.execute("ALTER TABLE meetings ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mom_records ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE meeting_embeddings ENABLE ROW LEVEL SECURITY;")

    op.execute("""
        CREATE POLICY tenant_isolation_meetings ON meetings
            USING (org_id = current_setting('app.current_org')::uuid);
    """)

    # mom_records has no direct org_id column — policy joins through meetings.
    # The simpler and safer approach: an app-layer WHERE on meeting_id is always
    # present, so we enforce RLS via the meeting FK:
    op.execute("""
        CREATE POLICY tenant_isolation_mom_records ON mom_records
            USING (
                EXISTS (
                    SELECT 1 FROM meetings m
                    WHERE m.id = mom_records.meeting_id
                      AND m.org_id = current_setting('app.current_org')::uuid
                )
            );
    """)

    op.execute("""
        CREATE POLICY tenant_isolation_embeddings ON meeting_embeddings
            USING (org_id = current_setting('app.current_org')::uuid);
    """)


def downgrade() -> None:
    # Drop in reverse dependency order
    op.execute("DROP TABLE IF EXISTS meeting_embeddings CASCADE;")
    op.execute("DROP TABLE IF EXISTS mom_edit_history CASCADE;")
    op.execute("DROP TABLE IF EXISTS mom_records CASCADE;")
    op.execute("DROP TABLE IF EXISTS meetings CASCADE;")
    op.execute("DROP TABLE IF EXISTS users CASCADE;")
    op.execute("DROP TABLE IF EXISTS organizations CASCADE;")
