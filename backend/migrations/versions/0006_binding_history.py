"""Historical identity binding intervals for delayed evidence."""
from alembic import op
import sqlalchemy as sa
revision='0006'
down_revision='0005'
branch_labels=None
depends_on=None
def upgrade():op.create_table('bracelet_bindings',sa.Column('id',sa.String(),primary_key=True),sa.Column('bracelet_id',sa.String(),sa.ForeignKey('bracelets.id'),nullable=False),sa.Column('session_id',sa.String(),sa.ForeignKey('shopping_sessions.id'),nullable=False),sa.Column('rfid',sa.String(),nullable=False),sa.Column('markers',sa.JSON(),nullable=False),sa.Column('started',sa.Float(),nullable=False),sa.Column('ended',sa.Float(),nullable=True))
def downgrade():op.drop_table('bracelet_bindings')
