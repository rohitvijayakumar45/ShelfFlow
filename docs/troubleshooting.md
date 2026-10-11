# Troubleshooting

| Symptom | Check |
|---|---|
| API database error | Run migrations and seed with the same `.env` as the server. Confirm PostgreSQL readiness in Compose. |
| App request fails | API must run at 8000; Vite/Nginx proxies `/api`; inspect displayed error, Origin allowlist and expired JWT. |
| QR generation method error | Current app uses POST with `{}`; rebuild/reload old cached app shell. |
| Entry QR replay | Generate a fresh token; used/expired token is deliberately rejected. |
| Gate timeout | Inspect release/UID physically. Use audited recovery; do not repeat motion blindly. |
| Browser QR camera blocked | Phone needs trusted HTTPS and permission; use manual token fallback. Never repurpose shelf camera. |
| Cart not live | Check socket Origin, backend proxy and auth; reconnect receives a full versioned snapshot. |
| Receipt unavailable | Resolve pending/disputed evidence, then simulated payment; retry timeout/failure with new idempotency key. |
| Camera diagnostic only | Carrier pins/PSRAM remain unknown. Obtain schematic, fill board_config and choose correct chip target. |
| MODEL_UNAVAILABLE | No existing custom weights supplied. Train on actual SKUs; COCO weights are not SKU recognition. |
| Tiny markers unreadable | Decode original resolution, enlarge rigid marker face/border, improve lighting/angle; do not trust downscaled detail. |
| Mode B loses actions | Compare A/B frame gaps and ownership visibility; default A until measurement favors B. |
| Weight unchanged during action | Motion-triggered capture still runs; equal-weight pickup/return remains pending. |
| HX711 negative mass | Verify A+/A− polarity, calibration sign and tare; colours are not universal. |
| Drifting/noisy weight | Check mechanical isolation, force path, supply ripple, cable routing and actual module voltage. Never auto-tare occupied trays. |
| RFID read missing | Measure actual RC522 short zone and natural wrist path. Vision/weight collection continues regardless. |
| Pending every hardware action | Expected baseline behavior: physical ownership/timing unvalidated. Staff review; no proximity-based automatic charges. |
| LittleFS disabled | Explicitly initialize filesystem before sensing; runtime never auto-formats queued evidence. |
| Sensor queue overflow | Reported evidence loss requires assisted reconciliation. Restore broker/application ACKs; inspect flash capacity/endurance. |
| MQTT auth rejected | Check username/password, per-device ACL, payload device key and device_id. Broker QoS ACK does not mean SQL committed. |
| Older npm intercepts build | `node apps/build.mjs`; invoke current installation's npm CLI for dependency install. |
| Docker not found | Install Docker Desktop Linux-container runtime; local SQLite simulator remains runnable without it. |

Evidence review: do not confirm a candidate because bracelet/product occur in the same frame. Require a continuous ownership chain and matching sensor evidence, or dismiss/correct after assisted reconciliation. Archived original receipt plus audited adjustment is preferable to silently changing a paid ledger.
