"""Staff-provisioned customer credentials."""
from alembic import op
import sqlalchemy as sa
revision="0005"
down_revision="0004"
branch_labels=None
depends_on=None
def upgrade():op.create_table('customer_accounts',sa.Column('id',sa.String(),primary_key=True),sa.Column('username',sa.String(),nullable=False,unique=True),sa.Column('customer_id',sa.String(),sa.ForeignKey('customers.id'),nullable=False,unique=True),sa.Column('password_hash',sa.String(),nullable=False))
def downgrade():op.drop_table('customer_accounts')
