"""WaterOps 防汛物资档案与不可变流水。

Revision ID: 0025
Revises: 0024
"""

from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("specification", sa.String(240), nullable=False, server_default=""),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("storage_location", sa.String(160), nullable=False, server_default=""),
        sa.Column("category", sa.String(32), nullable=False, server_default="flood_control"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('government_reserve','flood_control')",
            name="ck_inventory_items_category",
        ),
    )
    for column in ("name", "storage_location", "category", "active"):
        op.create_index(f"ix_inventory_items_{column}", "inventory_items", [column])
    op.create_table(
        "inventory_transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "item_id",
            sa.String(36),
            sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("transaction_type", sa.String(24), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("handler_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("counterparty", sa.String(240), nullable=False, server_default=""),
        sa.Column("purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("document_no", sa.String(100), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "related_transaction_id",
            sa.String(36),
            sa.ForeignKey("inventory_transactions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("expected_return_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="confirmed"),
        sa.Column("void_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("client_request_id", sa.String(80), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_inventory_transactions_quantity_positive"),
        sa.CheckConstraint(
            "transaction_type IN ('inbound','outbound','loan','return','adjustment_in','adjustment_out')",
            name="ck_inventory_transactions_type",
        ),
        sa.CheckConstraint("status IN ('confirmed','voided')", name="ck_inventory_transactions_status"),
    )
    for column in (
        "item_id",
        "transaction_type",
        "occurred_at",
        "handler_id",
        "related_transaction_id",
        "expected_return_at",
        "status",
    ):
        op.create_index(f"ix_inventory_transactions_{column}", "inventory_transactions", [column])
    op.create_index(
        "ix_inventory_transactions_client_request_id",
        "inventory_transactions",
        ["client_request_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("inventory_transactions")
    op.drop_table("inventory_items")
