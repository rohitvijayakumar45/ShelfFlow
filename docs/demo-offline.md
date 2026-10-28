# Offline demo on the hotspot (no internet needed)

The laptop runs everything. The staff computer/browser, the staff phone (QR scanner) and the customer
phone all join the **same hotspot** as the laptop and the camera, and open the apps over HTTPS from the
laptop's address. The hotspot does not need internet.

Phones only allow the camera on `https://` pages, so the apps are served with a certificate from a local
**ShelfFlow Demo CA**, made by `tools/demo_lan.py` (needs `openssl`, which Git for Windows includes).
Certificates live in `data/demo-certs/` (git-ignored; never share `ca.key`).

## Once per phone (do this before the demo)

1. Join the hotspot, start the setup page on the laptop:
   `.\.venv\Scripts\python.exe -m tools.demo_lan`
2. On the phone open the printed `http://<laptop-ip>:5180` page, download the certificate and install it:
   - **Android:** Settings → Security → More security settings → Encryption & credentials → Install a
     certificate → CA certificate.
   - **iPhone:** open the download → Settings → Profile Downloaded → Install, then Settings → General →
     About → Certificate Trust Settings → turn on *ShelfFlow Demo CA*.
3. Stop the setup page (Ctrl+C) when all phones are done.

The CA is made once and kept, so phones never need it again, even when the laptop's address changes.

## Every demo start

1. Laptop on the hotspot. Run `.\.venv\Scripts\python.exe -m tools.demo_lan --no-serve`. It issues a new
   certificate for the laptop's current address and prints the links.
2. Start the backend and the other services as usual (`tools.local_app backend`, `usb_bridge`,
   `camera_preview`, `local_app vision`, …), then the two apps in LAN mode:
   ```
   cd apps\staff;    node ..\node_modules\vite\bin\vite.js --mode lan --port 5174 --strictPort
   cd apps\customer; node ..\node_modules\vite\bin\vite.js --mode lan --port 5173 --strictPort
   ```
   (Claude: launch configs `staff-lan`, `customer-lan`, `demo-setup`.)
3. Open:
   - staff computer and staff phone: `https://<laptop-ip>:5174` (both sign in as staff),
   - customer phone: `https://<laptop-ip>:5173` (customer account, e.g. `shopper`).

The backend accepts the live-cart connection from any private hotspot address on ports 5173/5174
(`CORS_ORIGIN_REGEX`, set by `tools.local_app backend`), so a changed laptop address needs no config edit.

## If something does not connect

- **Phone can't open the page:** check the phone is on the same hotspot (not mobile data) and the link uses
  the laptop's current address. Some phone hotspots isolate clients; then use another phone or a small
  travel router as the hotspot.
- **Windows Firewall:** Node.js and Python must be allowed on the network type the hotspot has (this laptop
  allows both on Public). If not, an administrator runs once:
  `New-NetFirewallRule -DisplayName "ShelfFlow demo" -Direction Inbound -Protocol TCP -LocalPort 5173,5174,5180 -Action Allow -Profile Private,Public`
- **Certificate warning:** the phone has not installed/trusted the CA. *Advanced → Proceed* works for
  viewing, but the QR scanner may not get the camera. Install the CA.
- **QR scanner opens the selfie camera:** the scanner asks for the back camera; if a phone ignores that,
  paste the code shown under the customer's QR (*Manual token fallback*).
