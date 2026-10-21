"""Active-learning queue: crops vision could not identify, labelled by staff."""
from alembic import op
import sqlalchemy as sa
revision='0011'
down_revision='0010'
branch_labels=None
depends_on=None
def upgrade():
    op.create_table('uncertain_crops',
        sa.Column('id',sa.String(),primary_key=True),
        sa.Column('file',sa.String(),nullable=False),
        sa.Column('tray_id',sa.String(),nullable=True),
        sa.Column('track',sa.String(),nullable=False),
        sa.Column('top_product',sa.String(),nullable=True),
        sa.Column('second_product',sa.String(),nullable=True),
        sa.Column('similarity',sa.Float(),nullable=False),
        sa.Column('margin',sa.Float(),nullable=False),
        sa.Column('status',sa.String(),nullable=False),
        sa.Column('answer_product',sa.String(),nullable=True),
        sa.Column('reference_id',sa.String(),nullable=True),
        sa.Column('actor',sa.String(),nullable=True),
        sa.Column('created',sa.Float(),nullable=False),
        sa.Column('resolved',sa.Float(),nullable=True))
def downgrade():
    op.drop_table('uncertain_crops')
