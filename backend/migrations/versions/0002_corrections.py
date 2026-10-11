"""Audited corrections and tray history."""
from alembic import op
import sqlalchemy as sa
revision="0002"
down_revision="0001"
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('corrections',sa.Column('id',sa.String(),primary_key=True),sa.Column('session_id',sa.String(),sa.ForeignKey('shopping_sessions.id'),nullable=False),sa.Column('hypothesis_id',sa.String(),sa.ForeignKey('interaction_hypotheses.id'),nullable=False,unique=True),sa.Column('detail',sa.JSON(),nullable=False),sa.Column('created',sa.Float(),nullable=False))
    op.create_table('tray_assignments',sa.Column('id',sa.String(),primary_key=True),sa.Column('tray_id',sa.String(),sa.ForeignKey('trays.id'),nullable=False),sa.Column('product_id',sa.String(),sa.ForeignKey('products.id'),nullable=False),sa.Column('created',sa.Float(),nullable=False))
def downgrade():
    op.drop_table('corrections')
    op.drop_table('tray_assignments')
