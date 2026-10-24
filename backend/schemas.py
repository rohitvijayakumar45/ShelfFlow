from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Health(StrictModel):
    ok: bool = True
    flags: list[str] = Field(default_factory=list, max_length=30)
    queue_overflow: int = Field(default=0, ge=0)

class CellWeight(StrictModel):
    """One load cell of a multi-cell (tripod) tray."""
    name: str = Field(min_length=1, max_length=4)
    raw: float | None = None
    pre_g: float | None = None
    post_g: float | None = None
    ok: bool = True

class Position(StrictModel):
    """Tray-plane location (mm, tray geometry frame) of the load that changed."""
    x_mm: float
    y_mm: float

class Weight(StrictModel):
    pre_g: float
    post_g: float
    raw: float | None = None
    filtered: float | None = None
    noise_g: float = Field(ge=0)
    settled: bool
    cells: list[CellWeight] | None = Field(default=None, max_length=8)
    position_mm: Position | None = None
    geometry_version: str | None = None

class CellCalibration(StrictModel):
    name: str = Field(min_length=1, max_length=4)
    zero_raw: float
    counts_per_g: float

class CalibrationReport(StrictModel):
    """A calibration performed on the device (staff service mode), reported for the audit trail."""
    method: Literal["SERVICE_MODE_3_POSITION", "SERVICE_MODE_2_POSITION", "COMMAND"]
    known_mass_g: float = Field(gt=0)
    cells: list[CellCalibration] = Field(min_length=1, max_length=8)
    verify_total_g: float | None = None

class Candidate(StrictModel):
    marker_ids: list[int] = Field(default_factory=list, max_length=16)
    rfid_uid: str | None = None
    product_id: str
    product_track: str
    hand_track: str
    action: Literal["PICKUP", "RETURN"]
    quantity: int = Field(ge=1, le=10)
    # Separate evidence dimensions; these are rule inputs, not calibrated charge probabilities.
    identity: bool = False
    hand_product: bool = False
    trajectory: bool = False
    sku: bool = False
    temporal: bool = False
    bracelet_hand: bool = False
    opposite_hand: bool = False
    handoff: bool = False
    crossed: bool = False
    occluded: bool = False
    detector_confidence: float = Field(default=0, ge=0, le=1)
    # Unit-tracked trays: tray spot that the camera saw emptied (pickup) or filled (return).
    spot: int | None = Field(default=None, ge=0, le=31)
    chain_refs: list[str] = Field(default_factory=list, max_length=30)
    # Recognition scores behind product_id (similarities, not probabilities). Staff decisions on
    # reviewed events turn these into labelled data for re-tuning the gallery thresholds.
    similarity: float | None = Field(default=None, ge=-1, le=1)
    margin: float | None = Field(default=None, ge=-2, le=2)
    top_product: str | None = Field(default=None, max_length=64)
    second_product: str | None = Field(default=None, max_length=64)
    via: str | None = Field(default=None, max_length=24)

class DeviceEvent(StrictModel):
    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1, max_length=100)
    event_type: Literal["INTERACTION", "WEIGHT", "RFID", "HEALTH", "BARRIER", "CALIBRATION"]
    device_id: str
    boot_id: str
    sequence_number: int = Field(ge=0)
    shelf_id: str = "shelf-1"
    tray_id: str = "tray-a"
    capture_start: float
    capture_end: float
    monotonic_ms: int = Field(ge=0)
    clock_synced: bool
    clock_uncertainty_ms: float = Field(ge=0)
    calibration_version: str
    weight: Weight | None = None
    calibration: CalibrationReport | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    candidates: list[Candidate] = Field(default_factory=list, max_length=20)
    sensor_health: Health = Field(default_factory=Health)
    frame_age_ms: float = Field(default=0, ge=0)
    blur: bool = False
    frame_gap_ms: float = Field(default=0, ge=0)
    model_status: Literal["MOCK", "TRAINED", "MODEL_UNAVAILABLE"] = "MODEL_UNAVAILABLE"
    # Model registry: which detector / gallery / embedder / thresholds produced this event.
    model_versions: dict[str, str] = Field(default_factory=dict, max_length=12)

    @model_validator(mode="after")
    def ordered_time(self):
        if self.capture_end < self.capture_start:
            raise ValueError("capture_end precedes capture_start")
        if (self.event_type == "CALIBRATION") != (self.calibration is not None):
            raise ValueError("calibration report is required exactly for CALIBRATION events")
        return self
