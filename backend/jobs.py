import os
import time
from pathlib import Path
from sqlalchemy import select
from backend.models import Evidence, GateTransaction, Bracelet
from backend.service import transaction, audit

def maintenance():
    root = Path(os.getenv("EVIDENCE_ROOT", "evidence")).resolve()
    days = int(os.getenv("EVIDENCE_RETENTION_DAYS", "7"))
    with transaction() as db:
        for e in db.scalars(select(Evidence).where(Evidence.expires < time.time())):
            for ref in e.references:
                path = (root / ref).resolve()
                if path.parent == root and path.is_file():
                    path.unlink()
            e.references = ["DELETED_BY_RETENTION"]
        for g in db.scalars(select(GateTransaction).where(GateTransaction.state == "DISPENSING", GateTransaction.created < time.time() - 20)):
            g.state, g.detail = "FAULTED", "ACK_TIMEOUT_UNCERTAIN_RELEASE"
            db.get(Bracelet, g.bracelet_id).state = "FAULTED"
            audit(db, "maintenance", "GATE_TIMEOUT", {"command_id": g.id})
    # Orphaned captures expire too; audit ledger remains.
    if root.is_dir():
        for path in root.glob("*"):
            if path.is_file() and path.suffix in (".jpg", ".json") and path.stat().st_mtime < time.time() - days * 86400:
                path.unlink()
    return {"ok": True}

if __name__ == "__main__":
    from redis import Redis
    from rq import Queue
    queue = Queue(connection=Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0")))
    while True:
        queue.enqueue(maintenance, job_timeout=60, result_ttl=3600)
        time.sleep(30)
