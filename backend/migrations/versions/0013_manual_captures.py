"""Immediate camera captures with reviewed SAM segmentation."""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'
branch_labels = depends_on = None


def upgrade():
    op.create_table('manual_captures',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('request_key', sa.String(), nullable=False, unique=True),
        sa.Column('product_id', sa.String(), sa.ForeignKey('products.id'), nullable=False),
        sa.Column('tray_id', sa.String(), sa.ForeignKey('trays.id'), nullable=False),
        sa.Column('original_file', sa.String(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False), sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), nullable=False), sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('box', sa.JSON(), nullable=True), sa.Column('crop_file', sa.String(), nullable=True),
        sa.Column('plain_file', sa.String(), nullable=True), sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('reference_ids', sa.JSON(), nullable=False),
        sa.Column('created', sa.Float(), nullable=False), sa.Column('updated', sa.Float(), nullable=False))


def downgrade():
    op.drop_table('manual_captures')
