from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class Health(StrictModel):
    ok: bool = True
    flags: list[str] = Field(default_factory=list, max_length=30)
    queue_overflow: int = Field(default=0, ge=0)

class Weight(StrictModel):
    pre_g: float
    post_g: float
    raw: float | None = None
    filtered: float | None = None
    noise_g: float = Field(ge=0)
    settled: bool

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

class DeviceEvent(StrictModel):
    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1, max_length=100)
    event_type: Literal["INTERACTION", "WEIGHT", "RFID", "HEALTH", "BARRIER"]
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
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    candidates: list[Candidate] = Field(default_factory=list, max_length=20)
    sensor_health: Health = Field(default_factory=Health)
    frame_age_ms: float = Field(default=0, ge=0)
    blur: bool = False
    frame_gap_ms: float = Field(default=0, ge=0)
    model_status: Literal["MOCK", "TRAINED", "MODEL_UNAVAILABLE"] = "MODEL_UNAVAILABLE"

    @model_validator(mode="after")
    def ordered_time(self):
        if self.capture_end < self.capture_start:
            raise ValueError("capture_end precedes capture_start")
        return self
