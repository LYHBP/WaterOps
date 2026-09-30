"""WaterOps 公共目录与资料能力。

Revision ID: 0024
Revises: 0023
"""

from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "water_work_categories",
        sa.Column("code", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_water_work_categories_active", "water_work_categories", ["active"])
    op.create_table(
        "water_work_projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("category_code", sa.String(64), sa.ForeignKey("water_work_categories.code"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("frequency_hint", sa.String(120), nullable=False, server_default=""),
        sa.Column("archive_rule", sa.String(240), nullable=False, server_default=""),
        sa.Column("owner_hint", sa.String(80), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("category_code", "name"),
    )
    op.create_index("ix_water_work_projects_category_code", "water_work_projects", ["category_code"])
    op.create_index("ix_water_work_projects_active", "water_work_projects", ["active"])
    op.create_table(
        "water_work_resources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("category_code", sa.String(64), sa.ForeignKey("water_work_categories.code"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("water_work_projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("resource_type", sa.String(24), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("external_url", sa.String(2048), nullable=False, server_default=""),
        sa.Column("blob_sha256", sa.String(64), sa.ForeignKey("file_blobs.sha256"), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_water_work_resources_category_code", "water_work_resources", ["category_code"])
    op.create_index("ix_water_work_resources_project_id", "water_work_resources", ["project_id"])
    op.create_index("ix_water_work_resources_resource_type", "water_work_resources", ["resource_type"])
    op.create_index("ix_water_work_resources_active", "water_work_resources", ["active"])


def downgrade() -> None:
    op.drop_table("water_work_resources")
    op.drop_table("water_work_projects")
    op.drop_table("water_work_categories")
