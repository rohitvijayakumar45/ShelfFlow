import hashlib
from backend.db import SessionLocal
from backend.models import Bracelet, Product, Tray, Shelf, Device
from backend.config import DEVICE_SECRET,MODE

# The store's real catalogue: the products the shelf camera is trained on (prices in paise).
# Grams are only used by the simulator, which needs a unit weight; real weights come from tray loading.
STORE_PRODUCTS = [("sanitizer", "Sanitizer", 2500, 60), ("vicks", "Vicks", 19900, 50), ("sunscreen", "Sunscreen", 9900, 110),
                  ("hair-care", "Hair Wax", 25000, 80), ("powder", "Banana Powder", 15000, 120), ("nose-drops", "Nose Drops", 12000, 25)]
# Fixed catalogue the automated tests are written against (never shown to staff or shoppers).
TEST_PRODUCTS = [("oat", "Oat milk", 6500, 1030), ("coffee", "Cold brew", 9500, 330), ("bar", "Cocoa oat bar", 4000, 45), ("chips", "Sea salt crisps", 5500, 60)]

def seed(products=None):
    products = products or STORE_PRODUCTS
    with SessionLocal.begin() as db:
        if db.get(Shelf, "shelf-1"):
            return
        db.add(Shelf(id="shelf-1", camera_config={"status": "UNCONFIGURED", "mode": "A"}))
        for id_, name, price, grams in products:
            db.add(Product(id=id_, name=name, price_minor=price, unit_g=grams if MODE=='simulation' else None, tolerance_g=3 if MODE=='simulation' else None, weight_source="SIMULATED" if MODE=='simulation' else 'UNMEASURED'))
        db.flush()
        db.add_all([Tray(id="tray-a", shelf_id="shelf-1", product_id=products[0][0], stock=10 if MODE=='simulation' else 0, calibration_version="sim-v1" if MODE=='simulation' else 'UNSET', roi=[0, 0, 0.5, 1]), Tray(id="tray-b", shelf_id="shelf-1", product_id=products[1][0], stock=10 if MODE=='simulation' else 0, calibration_version="sim-v1" if MODE=='simulation' else 'UNSET', roi=[0.5, 0, 1, 1])])
        for i in range(1, 5) if MODE=='simulation' else []:
            db.add(Bracelet(id=f"B{i:02}", rfid=f"04AABB{i:02}", markers=[i, i + 100]))
        for id_ in ("simulator", "sensor-1", "vision-1", "gate-1"):
            db.add(Device(id=id_, secret_digest=hashlib.sha256(DEVICE_SECRET.encode()).hexdigest()))

if __name__ == "__main__":
    seed()
