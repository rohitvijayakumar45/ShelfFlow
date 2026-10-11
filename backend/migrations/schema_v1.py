"""Frozen initial database schema; independent of evolving ORM models."""
import sqlalchemy as sa
metadata = sa.MetaData()

sa.Table('audit_records', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('actor', sa.String(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
)

sa.Table('customers', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('alias', sa.String(), nullable=False),
)

sa.Table('devices', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('boot_id', sa.String(), nullable=False),
    sa.Column('max_sequence', sa.Integer(), nullable=False),
    sa.Column('last_seen', sa.Float(), nullable=False),
    sa.Column('health', sa.JSON(), nullable=False),
    sa.Column('secret_digest', sa.String(), nullable=False),
)

sa.Table('outbox', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('topic', sa.String(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('delivered', sa.Boolean(), nullable=False),
)

sa.Table('products', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('price_minor', sa.Integer(), nullable=False),
    sa.Column('unit_g', sa.Float(), nullable=True),
    sa.Column('tolerance_g', sa.Float(), nullable=True),
    sa.Column('weight_source', sa.String(), nullable=False),
)

sa.Table('shelves', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('camera_config', sa.JSON(), nullable=False),
)

sa.Table('sensor_events', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('device_id', sa.String(), sa.ForeignKey('devices.id'), nullable=False),
    sa.Column('boot_id', sa.String(), nullable=False),
    sa.Column('sequence_number', sa.Integer(), nullable=False),
    sa.Column('received', sa.Float(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('processed', sa.Boolean(), nullable=False),
    sa.UniqueConstraint('device_id', 'boot_id', 'sequence_number'),
)

sa.Table('shopping_sessions', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('customer_id', sa.String(), sa.ForeignKey('customers.id'), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('cart_version', sa.Integer(), nullable=False),
)

sa.Table('trays', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('shelf_id', sa.String(), sa.ForeignKey('shelves.id'), nullable=False),
    sa.Column('product_id', sa.String(), sa.ForeignKey('products.id'), nullable=False),
    sa.Column('stock', sa.Integer(), nullable=False),
    sa.Column('calibration_version', sa.String(), nullable=False),
    sa.Column('roi', sa.JSON(), nullable=False),
)

sa.Table('bracelets', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('rfid', sa.String(), nullable=False),
    sa.Column('markers', sa.JSON(), nullable=False),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('clean', sa.Boolean(), nullable=False),
    sa.Column('session_id', sa.String(), sa.ForeignKey('shopping_sessions.id'), nullable=True),
    sa.UniqueConstraint('session_id'),
    sa.UniqueConstraint('rfid'),
)

sa.Table('calibrations', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('tray_id', sa.String(), sa.ForeignKey('trays.id'), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('metadata', sa.JSON(), nullable=False),
)

sa.Table('cart_items', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('session_id', sa.String(), sa.ForeignKey('shopping_sessions.id'), nullable=False),
    sa.Column('product_id', sa.String(), sa.ForeignKey('products.id'), nullable=False),
    sa.Column('tray_id', sa.String(), sa.ForeignKey('trays.id'), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('price_minor', sa.Integer(), nullable=False),
    sa.UniqueConstraint('session_id', 'product_id'),
)

sa.Table('entry_tokens', metadata,
    sa.Column('digest', sa.String(), primary_key=True, nullable=False),
    sa.Column('session_id', sa.String(), sa.ForeignKey('shopping_sessions.id'), nullable=False),
    sa.Column('expires', sa.Float(), nullable=False),
    sa.Column('used', sa.Boolean(), nullable=False),
)

sa.Table('evidence', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('event_id', sa.String(), sa.ForeignKey('sensor_events.id'), nullable=False),
    sa.Column('references', sa.JSON(), nullable=False),
    sa.Column('expires', sa.Float(), nullable=False),
)

sa.Table('interaction_hypotheses', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('event_id', sa.String(), sa.ForeignKey('sensor_events.id'), nullable=False),
    sa.Column('session_id', sa.String(), sa.ForeignKey('shopping_sessions.id'), nullable=True),
    sa.Column('tray_id', sa.String(), sa.ForeignKey('trays.id'), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('reason', sa.JSON(), nullable=False),
    sa.Column('proposal', sa.JSON(), nullable=False),
)

sa.Table('inventory_movements', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('tray_id', sa.String(), sa.ForeignKey('trays.id'), nullable=False),
    sa.Column('delta', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(), nullable=False),
    sa.Column('reference', sa.String(), nullable=False),
    sa.UniqueConstraint('reference'),
)

sa.Table('orders', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('session_id', sa.String(), sa.ForeignKey('shopping_sessions.id'), nullable=False),
    sa.Column('total_minor', sa.Integer(), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.UniqueConstraint('session_id'),
)

sa.Table('cart_events', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('hypothesis_id', sa.String(), sa.ForeignKey('interaction_hypotheses.id'), nullable=False),
    sa.Column('session_id', sa.String(), sa.ForeignKey('shopping_sessions.id'), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('delta', sa.Integer(), nullable=False),
    sa.Column('product_id', sa.String(), nullable=False),
    sa.UniqueConstraint('hypothesis_id'),
)

sa.Table('gate_transactions', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('session_id', sa.String(), sa.ForeignKey('shopping_sessions.id'), nullable=False),
    sa.Column('bracelet_id', sa.String(), sa.ForeignKey('bracelets.id'), nullable=False),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('detail', sa.String(), nullable=False),
)

sa.Table('payments', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('order_id', sa.String(), sa.ForeignKey('orders.id'), nullable=False),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('adapter', sa.String(), nullable=False),
)

sa.Table('receipts', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('order_id', sa.String(), sa.ForeignKey('orders.id'), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
    sa.Column('content', sa.JSON(), nullable=False),
    sa.UniqueConstraint('order_id'),
)

sa.Table('review_decisions', metadata,
    sa.Column('id', sa.String(), primary_key=True, nullable=False),
    sa.Column('hypothesis_id', sa.String(), sa.ForeignKey('interaction_hypotheses.id'), nullable=False),
    sa.Column('actor', sa.String(), nullable=False),
    sa.Column('decision', sa.JSON(), nullable=False),
)
