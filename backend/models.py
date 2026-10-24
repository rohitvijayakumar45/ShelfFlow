import time
import uuid
from sqlalchemy import String, Integer, Float, Boolean, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from backend.db import Base

def uid():
    return str(uuid.uuid4())

class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    alias: Mapped[str] = mapped_column(String)

class ShoppingSession(Base):
    __tablename__ = "shopping_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"))
    status: Mapped[str] = mapped_column(String, default="ENTRY")
    cart_version: Mapped[int] = mapped_column(Integer, default=0)

class Account(Base):
    __tablename__ = "customer_accounts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String, unique=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), unique=True)
    password_hash: Mapped[str] = mapped_column(String)

class EntryToken(Base):
    __tablename__ = "entry_tokens"
    digest: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("shopping_sessions.id"))
    expires: Mapped[float] = mapped_column(Float)
    used: Mapped[bool] = mapped_column(Boolean, default=False)

class Bracelet(Base):
    __tablename__ = "bracelets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    rfid: Mapped[str] = mapped_column(String, unique=True)
    markers: Mapped[list] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String, default="AVAILABLE")
    clean: Mapped[bool] = mapped_column(Boolean, default=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("shopping_sessions.id"), unique=True, nullable=True)

class BraceletBinding(Base):
    __tablename__ = "bracelet_bindings"
    id: Mapped[str] = mapped_column(String,primary_key=True,default=uid)
    bracelet_id: Mapped[str] = mapped_column(ForeignKey('bracelets.id'))
    session_id: Mapped[str] = mapped_column(ForeignKey('shopping_sessions.id'))
    rfid: Mapped[str] = mapped_column(String)
    markers: Mapped[list] = mapped_column(JSON)
    started: Mapped[float] = mapped_column(Float,default=time.time)
    ended: Mapped[float | None] = mapped_column(Float,nullable=True)

class GateTransaction(Base):
    __tablename__ = "gate_transactions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("shopping_sessions.id"))
    bracelet_id: Mapped[str] = mapped_column(ForeignKey("bracelets.id"))
    state: Mapped[str] = mapped_column(String, default="DISPENSING")
    created: Mapped[float] = mapped_column(Float, default=time.time)
    detail: Mapped[str] = mapped_column(String, default="")

class Device(Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    boot_id: Mapped[str] = mapped_column(String, default="")
    max_sequence: Mapped[int] = mapped_column(Integer, default=-1)
    last_seen: Mapped[float] = mapped_column(Float, default=0)
    health: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_digest: Mapped[str] = mapped_column(String)

class Shelf(Base):
    __tablename__ = "shelves"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    camera_config: Mapped[dict] = mapped_column(JSON, default=dict)

class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    price_minor: Mapped[int] = mapped_column(Integer)
    unit_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    tolerance_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_source: Mapped[str] = mapped_column(String, default="UNMEASURED")
    weight_distribution: Mapped[dict] = mapped_column(JSON, default=dict)

class Tray(Base):
    __tablename__ = "trays"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    shelf_id: Mapped[str] = mapped_column(ForeignKey("shelves.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    stock: Mapped[int] = mapped_column(Integer, default=0)
    calibration_version: Mapped[str] = mapped_column(String, default="UNSET")
    roi: Mapped[list] = mapped_column(JSON, default=list)
    # True: mixed-product tray tracked per physical unit (tray_units); product_id is then ignored
    # and stock always equals the number of ON_TRAY units.
    unit_tracking: Mapped[bool] = mapped_column(Boolean, default=False)
    # Multi-cell (tripod) trays: {"version": str, "cells": [{"name", "x_mm", "y_mm"}]}.
    geometry: Mapped[dict] = mapped_column(JSON, default=dict)

class TrayLoad(Base):
    __tablename__ = "tray_loads"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    tray_id: Mapped[str] = mapped_column(ForeignKey("trays.id"))
    actor: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="OPEN")
    started: Mapped[float] = mapped_column(Float, default=time.time)
    ended: Mapped[float | None] = mapped_column(Float, nullable=True)

class TrayUnit(Base):
    """One physical item, weighed individually from a stable tray weight event when loaded."""
    __tablename__ = "tray_units"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    tray_id: Mapped[str] = mapped_column(ForeignKey("trays.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    load_id: Mapped[str] = mapped_column(ForeignKey("tray_loads.id"))
    source_event_id: Mapped[str] = mapped_column(ForeignKey("sensor_events.id"), unique=True)
    weight_g: Mapped[float] = mapped_column(Float)
    noise_g: Mapped[float] = mapped_column(Float)
    spot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Where on the tray the unit's weight landed (from the load-cell split), if the tray has geometry.
    x_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    y_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    crop_refs: Mapped[list] = mapped_column(JSON, default=list)
    # Few-shot enrolment of this unit's look: PENDING (stills waiting for vision), DONE, NO_STILLS, FAILED:<reason>
    reference_status: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="ON_TRAY")  # ON_TRAY | IN_CART | REMOVED
    session_id: Mapped[str | None] = mapped_column(ForeignKey("shopping_sessions.id"), nullable=True)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    updated: Mapped[float] = mapped_column(Float, default=time.time)

class AssistedStock(Base):
    """Temporary, staff-counted stock; never represented as weighed TrayUnits."""
    __tablename__ = "assisted_stock"
    tray_id: Mapped[str] = mapped_column(ForeignKey("trays.id"), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)


class AssistedAction(Base):
    __tablename__ = "assisted_actions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("shopping_sessions.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created: Mapped[float] = mapped_column(Float, default=time.time)


class Calibration(Base):
    __tablename__ = "calibrations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    tray_id: Mapped[str] = mapped_column(ForeignKey("trays.id"))
    created: Mapped[float] = mapped_column(Float, default=time.time)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON)

class TrayAssignment(Base):
    __tablename__ = "tray_assignments"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    tray_id: Mapped[str] = mapped_column(ForeignKey("trays.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    created: Mapped[float] = mapped_column(Float, default=time.time)

class Correction(Base):
    __tablename__ = "corrections"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("shopping_sessions.id"))
    hypothesis_id: Mapped[str] = mapped_column(ForeignKey("interaction_hypotheses.id"), unique=True)
    detail: Mapped[dict] = mapped_column(JSON)
    created: Mapped[float] = mapped_column(Float, default=time.time)

class FlushBarrier(Base):
    __tablename__ = "flush_barriers"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("shopping_sessions.id"))
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    requested: Mapped[float] = mapped_column(Float, default=time.time)
    acknowledged_event: Mapped[str | None] = mapped_column(String, nullable=True)

class SensorEvent(Base):
    __tablename__ = "sensor_events"
    __table_args__ = (UniqueConstraint("device_id", "boot_id", "sequence_number"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"))
    boot_id: Mapped[str] = mapped_column(String)
    sequence_number: Mapped[int] = mapped_column(Integer)
    received: Mapped[float] = mapped_column(Float, default=time.time)
    payload: Mapped[dict] = mapped_column(JSON)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)

class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("sensor_events.id"))
    references: Mapped[list] = mapped_column(JSON)
    expires: Mapped[float] = mapped_column(Float)

class SensorClaim(Base):
    __tablename__ = "sensor_claims"
    source_event_id: Mapped[str] = mapped_column(ForeignKey('sensor_events.id'),primary_key=True)
    interaction_event_id: Mapped[str] = mapped_column(ForeignKey('sensor_events.id'))

class Hypothesis(Base):
    __tablename__ = "interaction_hypotheses"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    event_id: Mapped[str] = mapped_column(ForeignKey("sensor_events.id"))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("shopping_sessions.id"), nullable=True)
    tray_id: Mapped[str] = mapped_column(ForeignKey("trays.id"))
    status: Mapped[str] = mapped_column(String, default="PENDING_REVIEW")
    reason: Mapped[list] = mapped_column(JSON)
    proposal: Mapped[dict] = mapped_column(JSON)

class CartItem(Base):
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("session_id", "product_id"),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("shopping_sessions.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    tray_id: Mapped[str] = mapped_column(ForeignKey("trays.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    price_minor: Mapped[int] = mapped_column(Integer)

class CartEvent(Base):
    __tablename__ = "cart_events"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    hypothesis_id: Mapped[str] = mapped_column(ForeignKey("interaction_hypotheses.id"), unique=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("shopping_sessions.id"))
    version: Mapped[int] = mapped_column(Integer)
    delta: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[str] = mapped_column(String)

class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    tray_id: Mapped[str] = mapped_column(ForeignKey("trays.id"))
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String)
    reference: Mapped[str] = mapped_column(String, unique=True)

class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    hypothesis_id: Mapped[str] = mapped_column(ForeignKey("interaction_hypotheses.id"))
    actor: Mapped[str] = mapped_column(String)
    decision: Mapped[dict] = mapped_column(JSON)

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("shopping_sessions.id"), unique=True)
    total_minor: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default="UNPAID")

class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    state: Mapped[str] = mapped_column(String)
    adapter: Mapped[str] = mapped_column(String, default="SIMULATED")

class Receipt(Base):
    __tablename__ = "receipts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), unique=True)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    content: Mapped[dict] = mapped_column(JSON)

class Audit(Base):
    __tablename__ = "audit_records"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    detail: Mapped[dict] = mapped_column(JSON)
    created: Mapped[float] = mapped_column(Float, default=time.time)

class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    topic: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)


class ProductReference(Base):
    """One reference crop of a product for the recognition gallery (few-shot, no retraining)."""
    __tablename__ = "product_references"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    unit_id: Mapped[str | None] = mapped_column(ForeignKey("tray_units.id"), nullable=True)
    teach_id: Mapped[str | None] = mapped_column(ForeignKey("teach_sessions.id"), nullable=True)
    source: Mapped[str] = mapped_column(String)  # RESTOCK_AUTO | TEACH | STAFF_LABEL
    file: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")  # ACTIVE | REJECTED
    box: Mapped[list | None] = mapped_column(JSON, nullable=True)
    quality: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    updated: Mapped[float] = mapped_column(Float, default=time.time)

class TeachSession(Base):
    """Staff shows a product to the camera; vision keeps diverse views as references."""
    __tablename__ = "teach_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    tray_id: Mapped[str] = mapped_column(ForeignKey("trays.id"))
    actor: Mapped[str] = mapped_column(String)
    started: Mapped[float] = mapped_column(Float, default=time.time)
    until: Mapped[float] = mapped_column(Float)
    max_refs: Mapped[int] = mapped_column(Integer, default=20)
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="ACTIVE")  # ACTIVE | DONE | STOPPED

class UncertainCrop(Base):
    """A crop vision could not identify with confidence; staff name it and it becomes a reference."""
    __tablename__ = "uncertain_crops"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    file: Mapped[str] = mapped_column(String)
    tray_id: Mapped[str | None] = mapped_column(String, nullable=True)
    track: Mapped[str] = mapped_column(String)
    top_product: Mapped[str | None] = mapped_column(String, nullable=True)
    second_product: Mapped[str | None] = mapped_column(String, nullable=True)
    similarity: Mapped[float] = mapped_column(Float)
    margin: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String, default="OPEN")  # OPEN | LABELLED | DISCARDED
    answer_product: Mapped[str | None] = mapped_column(String, nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String, nullable=True)
    actor: Mapped[str | None] = mapped_column(String, nullable=True)
    created: Mapped[float] = mapped_column(Float, default=time.time)
    resolved: Mapped[float | None] = mapped_column(Float, nullable=True)
