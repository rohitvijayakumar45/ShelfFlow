# Local hardware integration

The current prototype uses one mixed-product tray, three load cells, two RC522 readers and an OV3660 camera. Keep the main ESP32 on laptop USB during normal operation. The camera uses Wi-Fi and a separate power source. Its programmer is needed on laptop USB only for flashing.

## Start the application

Run each long-running command in its own terminal from the repository root:

```powershell
.\.venv\Scripts\python.exe -m tools.local_app setup
.\.venv\Scripts\python.exe -m tools.local_app backend
.\.venv\Scripts\python.exe -m tools.usb_bridge
.\.venv\Scripts\python.exe -m tools.camera_preview --camera http://CAMERA_IP
.\.venv\Scripts\python.exe -m tools.detector_preview
.\.venv\Scripts\python.exe -m tools.local_app vision
```

Replace CAMERA_IP with the IP printed by the camera after joining the hotspot. Set `PHYSICAL_CAMERA_URL` in ignored `data/integration/runtime.env` to the same camera URL before starting vision. Keep only one camera connection: the detector and vision collector consume the shared preview on port 8765.

Start the existing Vite customer and staff workspaces from `apps` using `npm run dev --workspace @shelfflow/customer` and `npm run dev --workspace @shelfflow/staff`.

| Page | Address | Local login |
|---|---|---|
| Staff | http://127.0.0.1:5174 | Password `ShelfFlow-Staff-2026!` |
| Customer | http://127.0.0.1:5173 | Username `shopper`, password `ShelfFlow-Shopper-2026!` |
| Detector | http://127.0.0.1:8766 | Local preview |
| API | http://127.0.0.1:8000/docs | API documentation |

These credentials are for this local prototype. Device keys, hotspot credentials and the integration database remain in ignored local files.

## Prepare real stock and a shopping session

1. Verify live controller health, saved calibration and camera preview in the staff app. Initial stock is zero; setup does not invent item weights or inventory.
2. Use the tray loading workflow to measure and register each physical unit. Select Sanitizer (₹25), Vicks (₹199) or Sunscreen (₹99). Finish loading before testing purchases.
3. Sign in as the customer. Staff must confirm cleaning and physical handover before assigning a bracelet. B01 has RFID `43D51829`; an RFID tap alone does not prove handover or ownership.
4. Pick up or return an item. The controller records weight changes; vision supplies product candidates. Staff review identifies the customer and the actual unit before committing a cart change.
5. Reconcile outstanding evidence before checkout. Payment is simulated. Hardware mode does not automatically attribute products to shoppers.

## Event delivery and recovery

The controller has a 256-event durable queue. The bridge freezes each payload locally before posting it and sends a controller ACK only after the backend confirms the matching event ID and durable commit. Network failures retain queued events for retry. Old reboot backlog retains its uncertainty and may require review.

Saved calibration is reported without writing new calibration to NVS. Do not erase flash, re-tare with items present, or run calibration while shopping. Stop the USB bridge before using COM3 for firmware uploads, and verify the camera MAC `68:09:47:F8:93:B8` before flashing camera firmware. The main controller MAC is `00:4B:12:EE:81:F4`.

The Yeti hotspot name observed on this laptop includes a trailing space (`Yeti `). Firmware SSIDs must match it exactly. A change of hotspot usually changes the camera IP; update the preview command and `PHYSICAL_CAMERA_URL`, then restart the preview and vision services.

## Validation boundary

The automated suite passes 130 tests; both apps passed TypeScript and production builds. Controller event delivery, saved calibration reporting and prices were checked against the running hardware backend. A complete physical stock-loading, customer-assignment, pickup/return and checkout trial remains necessary. The three-product model is a pilot; unknown objects require staff review and later dataset collection.
