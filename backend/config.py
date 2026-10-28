import os
from dotenv import load_dotenv

load_dotenv()
MODE = os.getenv("SHELFFLOW_MODE", "simulation")
WEIGHTS_ENABLED = os.getenv("WEIGHTS_ENABLED", "true").lower() not in ("false", "0", "no")
# Hardware mode: a camera interaction backed by an on-time shelf weight event may update the cart
# without staff review when every fusion check passes. Off: every physical interaction is reviewed.
AUTO_ADD = os.getenv("AUTO_ADD", "false").lower() in ("true", "1", "yes")
# Whose cart a shelf pickup goes to.
#   camera: the bracelet pattern at the hand in a camera INTERACTION event (bracelet + hand tracking).
#   rfid:   the shopper whose bracelet was last tapped on either shelf reader (or who just checked in)
#           owns every shelf weight step for SHELF_PRESENCE_S; the weighed units or the camera name the
#           product. One shopper at the shelf at a time (backend/shelf_cart.py).
#   hybrid: rfid while one shopper is at the shelf; with two or more, the camera event names the
#           bracelet at the hand that moved the item (camera), and an unreported step goes to review.
CART_ATTRIBUTION = os.getenv("CART_ATTRIBUTION", "camera")
SHELF_PRESENCE_S = float(os.getenv("SHELF_PRESENCE_S", "300"))
# The shelf controller counts every event it ever lost (queue_overflow) and keeps the count across reboots, so
# one old loss marks every later event unhealthy. Staff can accept a known count after checking it is history
# (e.g. bench tests): events at or below it are healthy again; any new loss raises the count and is refused.
SENSOR_ACCEPTED_OVERFLOW = int(os.getenv("SENSOR_ACCEPTED_OVERFLOW", "0"))
# The shelf controller's two RC522 readers: a tap on the entry reader identifies the bracelet staff hand
# over at check-in; a tap on the exit reader checks out. In rfid attribution both readers also mark the
# shopper at the shelf, so an exit tap bills only a visit whose customer pressed Checkout in the app first.
# Empty disables the role (taps on that reader are then ordinary shelf observations).
ENTRY_READER = os.getenv("RFID_ENTRY_READER", "R1")
EXIT_READER = os.getenv("RFID_EXIT_READER", "R2")
EXIT_NEEDS_CHECKOUT = CART_ATTRIBUTION in ("rfid", "hybrid")  # R2 bills only after Checkout in the app
if MODE not in ('simulation','hardware'):
    raise RuntimeError('SHELFFLOW_MODE must be simulation or hardware')
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./shelfflow.db")
JWT_SECRET = os.getenv("JWT_SECRET", "local-demo-change-me-32-characters")
STAFF_PASSWORD = os.getenv("STAFF_PASSWORD", "local-demo-staff")
DEVICE_SECRET = os.getenv("DEVICE_SECRET", "local-demo-device")
if MODE != "simulation" and any(v.startswith("local-demo") for v in (JWT_SECRET, STAFF_PASSWORD, DEVICE_SECRET)):
    raise RuntimeError("Hardware/deployment mode requires configured secrets")
