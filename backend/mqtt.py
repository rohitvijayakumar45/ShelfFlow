"""Durable ingest -> transaction outbox -> application ACK. MQTT QoS alone is insufficient."""
import os
import json
import time
import logging
import paho.mqtt.client as mqtt
from sqlalchemy import select
from backend.main import device_auth
from backend.schemas import DeviceEvent
from backend.service import ingest, gate_ack, transaction
from backend.models import Outbox

log = logging.getLogger("shelfflow.mqtt")

def run():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="shelfflow-ingestor")
    client.username_pw_set(os.environ["MQTT_USERNAME"], os.environ["MQTT_PASSWORD"])
    if os.getenv("MQTT_CA"):
        client.tls_set(os.environ["MQTT_CA"])
    client.reconnect_delay_set(1, 30)
    def connect(c, _, flags, rc, props):
        if rc == 0:
            c.subscribe("shelfflow/devices/+/events", qos=1)
            c.subscribe("shelfflow/devices/gate-1/gate-ack", qos=1)
    def message(c, _, msg):
        try:
            if len(msg.payload) > 2_000_000:
                raise ValueError("Oversize")
            device_id = msg.topic.split("/")[2]
            body = json.loads(msg.payload)
            key = body.pop("device_key", "")
            device_auth(device_id, key)
            if msg.topic.endswith("/gate-ack"):
                from backend.main import GateAck
                ack = GateAck(**body)
                result = gate_ack(**ack.model_dump(), actor=device_id)
                with transaction() as db:
                    db.add(Outbox(topic=f"shelfflow/devices/{device_id}/acks", payload={"command_id": ack.command_id, "state": result["state"], "durable": True}))
            else:
                event = DeviceEvent(**body)
                if event.device_id != device_id:
                    raise ValueError("Device scope")
                ingest(event)
        except Exception as exc:
            # Never log raw QR, auth or event data.
            log.warning("Rejected device message: %s", type(exc).__name__)
    client.on_connect, client.on_message = connect, message
    client.connect(os.getenv("MQTT_HOST", "localhost"), int(os.getenv("MQTT_PORT", "1883")), 30)
    client.loop_start()
    try:
        while True:
            with transaction() as db:
                rows = list(db.scalars(select(Outbox).where(Outbox.delivered == False).limit(100)))
                for row in rows:
                    if row.topic.startswith("cart/"):
                        # Authenticated sockets derive snapshots directly from durable state.
                        row.delivered = True
                        continue
                    if not client.is_connected():
                        break
                    info = client.publish(row.topic, json.dumps(row.payload), qos=1)
                    info.wait_for_publish(timeout=3)
                    if info.is_published():
                        row.delivered = True
            time.sleep(0.2)
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
