"""Exclusive sensor evidence allocation across interaction groups."""
from alembic import op
import sqlalchemy as sa
revision='0007'
down_revision='0006'
branch_labels=None
depends_on=None
def upgrade():op.create_table('sensor_claims',sa.Column('source_event_id',sa.String(),sa.ForeignKey('sensor_events.id'),primary_key=True),sa.Column('interaction_event_id',sa.String(),sa.ForeignKey('sensor_events.id'),nullable=False))
def downgrade():op.drop_table('sensor_claims')
