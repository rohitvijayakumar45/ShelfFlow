"""Unit-tracked mixed-product trays: individually weighed units and staff loading sessions."""
from alembic import op
import sqlalchemy as sa
revision='0008'
down_revision='0007'
branch_labels=None
depends_on=None
def upgrade():
    op.add_column('trays',sa.Column('unit_tracking',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.create_table('tray_loads',
        sa.Column('id',sa.String(),primary_key=True),
        sa.Column('tray_id',sa.String(),sa.ForeignKey('trays.id'),nullable=False),
        sa.Column('actor',sa.String(),nullable=False),
        sa.Column('reason',sa.String(),nullable=False),
        sa.Column('status',sa.String(),nullable=False),
        sa.Column('started',sa.Float(),nullable=False),
        sa.Column('ended',sa.Float(),nullable=True))
    op.create_table('tray_units',
        sa.Column('id',sa.String(),primary_key=True),
        sa.Column('tray_id',sa.String(),sa.ForeignKey('trays.id'),nullable=False),
        sa.Column('product_id',sa.String(),sa.ForeignKey('products.id'),nullable=False),
        sa.Column('load_id',sa.String(),sa.ForeignKey('tray_loads.id'),nullable=False),
        sa.Column('source_event_id',sa.String(),sa.ForeignKey('sensor_events.id'),nullable=False,unique=True),
        sa.Column('weight_g',sa.Float(),nullable=False),
        sa.Column('noise_g',sa.Float(),nullable=False),
        sa.Column('spot',sa.Integer(),nullable=True),
        sa.Column('crop_refs',sa.JSON(),nullable=False),
        sa.Column('status',sa.String(),nullable=False),
        sa.Column('session_id',sa.String(),sa.ForeignKey('shopping_sessions.id'),nullable=True),
        sa.Column('created',sa.Float(),nullable=False),
        sa.Column('updated',sa.Float(),nullable=False))
def downgrade():
    op.drop_table('tray_units')
    op.drop_table('tray_loads')
    with op.batch_alter_table('trays') as batch:
        batch.drop_column('unit_tracking')
