# Security and evidence policy

## Implemented prototype controls

- Customer JWT is scoped to one session; staff routes require staff JWT; tokens expire after one hour. Socket authentication is its first message; exact Origin allowlist is checked. No token in URL.
- Device HTTP key is checked against stored SHA-256 digest and path/payload device scope. MQTT additionally uses password authentication and per-user topic ACL. Versioned Pydantic payloads reject unknown fields/non-finite floats.
- Entry QR is opaque, short-lived, hashed, single-use and transactionally consumed. No personal information is embedded.
- Prices, session binding, cart deltas and payment outcome ledger are server-owned. Customer may request only a simulated adapter outcome; cannot supply amounts/quantities.
- Durable event-ID/boot/sequence replay guards, transactional cart/inventory/outbox and audited reviews/restocks/corrections.
- Evidence metadata/files require staff authentication. Fixed configured camera preview is staff-only. Paths are constrained to evidence root; tokens/QR contents and raw payloads are not logged by the ingestor.
- Configurable evidence deletion, retained audit ledger; no facial recognition. Device data and saved video are excluded from source control.

## Explicit development limits

Simulation has alias-only signup, known development secrets, fixture evidence and mocked measurements. It is loopback-only by default. Hardware mode refuses default secrets, disables alias-only enrollment and requires staff-assisted exit reconciliation. Staff can provision local customer accounts with salted scrypt password hashes; credentials resume an existing active visit or create a new visit after completion. Third-party identity-provider integration and unattended account recovery remain deployment work. No production security certification is claimed.

Rate limit is 600 HTTP requests/minute per client IP within one API process. Configure an edge limiter before exposed deployment; distributed limits and stronger login throttling are required. TLS, secret rotation/revocation, password hashing for individual staff accounts, audited role administration, device provisioning and security monitoring remain deployment work. Staff demo login uses a configured shared password; use individual identities for real audits.

MQTT dev firmware uses cleartext TCP and includes its device key in the event body; this is allowed only on a controlled prototype LAN. For deployment, use TLS broker/CA validation and a signed event/device credential adapter rather than publishing plaintext shared keys. Native laptop MQTT supports `MQTT_CA`, but firmware TLS is not implemented. One demo password for all broker users is not production provisioning. Do not expose development services to the Internet.

RFID UIDs and printed marker faces can be copied. Conflicting RFID/marker evidence rejects, but copying is not prevented. No facial recognition, crypto wrist credentials, active bracelets, or RFID-wide-shelf tracking claims.

## Retention

`EVIDENCE_ROOT` and `EVIDENCE_RETENTION_DAYS` (default 7) configure file retention. Metadata expiry is also recorded per evidence row. Scheduler/RQ maintenance removes expired files/references and orphaned capture files, while keeping event/audit metadata. Ensure all camera metadata JSON filenames share retention policy. A legal hold/export workflow is not yet implemented; do not claim it is. Obtain volunteer consent and use aliases in datasets. Store actual footage outside the repository with restricted filesystem access/backups.

Before deployment, front HTTP/WS with a trusted HTTPS proxy, enable secure browser contexts for phone scanning/PWA, restrict CORS, configure TLS-only MQTT, assign distinct secrets, protect PostgreSQL/Redis, and validate restore procedures. These are deployment prerequisites, not requests for additional hardware.
