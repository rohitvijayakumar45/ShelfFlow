"""Tripod tray geometry and per-unit positions from the load-cell weight split."""
from alembic import op
import sqlalchemy as sa
revision='0009'
down_revision='0008'
branch_labels=None
depends_on=None
def upgrade():
    op.add_column('trays',sa.Column('geometry',sa.JSON(),nullable=False,server_default='{}'))
    op.add_column('tray_units',sa.Column('x_mm',sa.Float(),nullable=True))
    op.add_column('tray_units',sa.Column('y_mm',sa.Float(),nullable=True))
def downgrade():
    with op.batch_alter_table('tray_units') as batch:
        batch.drop_column('y_mm')
        batch.drop_column('x_mm')
    with op.batch_alter_table('trays') as batch:
        batch.drop_column('geometry')
