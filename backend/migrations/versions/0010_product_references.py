"""Few-shot product references (restock auto-crops, teach mode) and teach sessions."""
from alembic import op
import sqlalchemy as sa
revision='0010'
down_revision='0009'
branch_labels=None
depends_on=None
def upgrade():
    op.add_column('tray_units',sa.Column('reference_status',sa.String(),nullable=True))
    op.create_table('teach_sessions',
        sa.Column('id',sa.String(),primary_key=True),
        sa.Column('product_id',sa.String(),sa.ForeignKey('products.id'),nullable=False),
        sa.Column('tray_id',sa.String(),sa.ForeignKey('trays.id'),nullable=False),
        sa.Column('actor',sa.String(),nullable=False),
        sa.Column('started',sa.Float(),nullable=False),
        sa.Column('until',sa.Float(),nullable=False),
        sa.Column('max_refs',sa.Integer(),nullable=False),
        sa.Column('accepted',sa.Integer(),nullable=False),
        sa.Column('status',sa.String(),nullable=False))
    op.create_table('product_references',
        sa.Column('id',sa.String(),primary_key=True),
        sa.Column('product_id',sa.String(),sa.ForeignKey('products.id'),nullable=False),
        sa.Column('unit_id',sa.String(),sa.ForeignKey('tray_units.id'),nullable=True),
        sa.Column('teach_id',sa.String(),sa.ForeignKey('teach_sessions.id'),nullable=True),
        sa.Column('source',sa.String(),nullable=False),
        sa.Column('file',sa.String(),nullable=False),
        sa.Column('status',sa.String(),nullable=False),
        sa.Column('box',sa.JSON(),nullable=True),
        sa.Column('quality',sa.JSON(),nullable=False),
        sa.Column('created',sa.Float(),nullable=False),
        sa.Column('updated',sa.Float(),nullable=False))
def downgrade():
    op.drop_table('product_references')
    op.drop_table('teach_sessions')
    with op.batch_alter_table('tray_units') as batch:
        batch.drop_column('reference_status')
