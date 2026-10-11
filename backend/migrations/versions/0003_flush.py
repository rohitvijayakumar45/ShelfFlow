"""Device reconciliation checkpoints."""
from alembic import op
import sqlalchemy as sa
revision="0003"
down_revision="0002"
branch_labels=None
depends_on=None
def upgrade():op.create_table('flush_barriers',sa.Column('id',sa.String(),primary_key=True),sa.Column('session_id',sa.String(),sa.ForeignKey('shopping_sessions.id'),nullable=False),sa.Column('device_id',sa.String(),sa.ForeignKey('devices.id'),nullable=False),sa.Column('requested',sa.Float(),nullable=False),sa.Column('acknowledged_event',sa.String(),nullable=True))
def downgrade():op.drop_table('flush_barriers')
