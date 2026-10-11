import os
from dotenv import load_dotenv

load_dotenv()
MODE = os.getenv("SHELFFLOW_MODE", "simulation")
if MODE not in ('simulation','hardware'):
    raise RuntimeError('SHELFFLOW_MODE must be simulation or hardware')
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./shelfflow.db")
JWT_SECRET = os.getenv("JWT_SECRET", "local-demo-change-me-32-characters")
STAFF_PASSWORD = os.getenv("STAFF_PASSWORD", "local-demo-staff")
DEVICE_SECRET = os.getenv("DEVICE_SECRET", "local-demo-device")
if MODE != "simulation" and any(v.startswith("local-demo") for v in (JWT_SECRET, STAFF_PASSWORD, DEVICE_SECRET)):
    raise RuntimeError("Hardware/deployment mode requires configured secrets")
