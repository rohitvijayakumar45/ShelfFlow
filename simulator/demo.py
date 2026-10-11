"""HTTP demo: simulation uses exactly the same ingestion and fusion route as devices."""
import argparse
import uuid
import httpx
from simulator.scenarios import interaction

def run(base, password, device_key):
    api = httpx.Client(base_url=base, timeout=10)
    def request(method, path, **kwargs):
        response = api.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None
    staff = request("POST", "/auth/staff", json={"password": password})["token"]
    login = request("POST", "/auth/customer", json={"alias": "Demo shopper"})
    headers = {"Authorization": f"Bearer {login['token']}"}
    sh = {"Authorization": f"Bearer {staff}"}
    qr = request("POST", "/customer/entry-token", headers=headers)
    gate = request("POST", "/gate/dispense", headers=sh, json={"token": qr["token"], "command_id": str(uuid.uuid4())})
    state = request("GET", "/staff/state", headers=sh)
    bracelet = next(b for b in state["bracelets"] if b["id"] == gate["bracelet_id"])
    request("POST", "/devices/gate-1/ack", headers={"X-Device-Key": device_key}, json={"command_id": gate["command_id"], "actual_uid": bracelet["rfid"], "released": True, "position_ok": True})
    seq = int(__import__("time").time() * 1000)
    for i, action in enumerate(["PICKUP", "RETURN", "PICKUP"]):
        event = interaction(marker=bracelet["markers"][0], action=action, sequence=seq + i)
        request("POST", "/devices/simulator/events", headers={"X-Device-Key": device_key}, json=event.model_dump())
        print(action, request("GET", "/customer/cart", headers=headers))
    request("POST", "/customer/checkout", headers=headers, json={"idempotency_key": str(uuid.uuid4()), "outcome": "SUCCESS"})
    print("SIMULATED RECEIPT", request("GET", "/customer/cart", headers=headers)["receipt"])
    request("POST", f"/staff/bracelets/{bracelet['id']}/return", headers=sh, json={"cleaned": True, "reason": "Simulation fixture return/cleaning; no physical bracelet was handled"})
    api.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--password", default="local-demo-staff")
    parser.add_argument("--device-key", default="local-demo-device")
    args = parser.parse_args()
    run(args.base, args.password, args.device_key)
