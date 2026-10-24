"""Counted inventory and audited operations while weight integration is unavailable."""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
branch_labels = depends_on = None


def upgrade():
    op.create_table('assisted_stock',
        sa.Column('tray_id', sa.String(), sa.ForeignKey('trays.id'), primary_key=True),
        sa.Column('product_id', sa.String(), sa.ForeignKey('products.id'), primary_key=True),
        sa.Column('quantity', sa.Integer(), nullable=False))
    op.create_table('assisted_actions',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('session_id', sa.String(), sa.ForeignKey('shopping_sessions.id'), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created', sa.Float(), nullable=False))


def downgrade():
    op.drop_table('assisted_actions')
    op.drop_table('assisted_stock')
