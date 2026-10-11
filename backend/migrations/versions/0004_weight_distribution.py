"""Measured unit-weight sample distributions."""
from alembic import op
import sqlalchemy as sa
revision="0004"
down_revision="0003"
branch_labels=None
depends_on=None
def upgrade():
    if 'weight_distribution' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('products')}:
        op.add_column('products',sa.Column('weight_distribution',sa.JSON(),nullable=False,server_default='{}'))
def downgrade():op.drop_column('products','weight_distribution')
