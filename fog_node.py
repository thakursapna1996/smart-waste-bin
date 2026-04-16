import time
import json
import threading
import logging
import os
import requests
from datetime import datetime, timezone
from collections import defaultdict
from dotenv import dotenv_values

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, validator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [FOG] [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

cfg = dotenv_values(".env")

CLOUD_URL        = cfg.get("CLOUD_URL",       "http://Smart-waste-bin-env.eba-zvrsdqbr.us-east-1.elasticbeanstalk.com/receive")
WINDOW_SECONDS   = float(cfg.get("WINDOW_SECONDS", "10"))   # aggregate window
VALID_SENSORS    = {"fill_level", "weight", "gas_odour", "temperature", "lid_status"}

# ---------------------------------------------------------------------------
# Pydantic model — validates every incoming sensor payload
# ---------------------------------------------------------------------------

class SensorPayload(BaseModel):
    bin_id:      str
    timestamp:   str
    sensor_type: str
    unit:        str
    value:       float | bool
    alert:       bool

    @validator("sensor_type")
    def sensor_must_be_known(cls, v):
        if v not in VALID_SENSORS:
            raise ValueError(f"Unknown sensor_type '{v}'. Must be one of {VALID_SENSORS}")
        return v

    @validator("bin_id")
    def bin_id_not_empty(cls, v):
        if not v.strip():
            raise ValueError("bin_id cannot be empty")
        return v

# ---------------------------------------------------------------------------
# In-memory aggregation buffer  { bin_id -> { sensor_type -> [readings] } }
# ---------------------------------------------------------------------------

buffer: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
buffer_lock = threading.Lock()

app = FastAPI(title="Smart Waste Bin — Fog Node", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "fog_node": "running", "time": datetime.now(timezone.utc).isoformat()}


@app.post("/ingest", status_code=202)
def ingest(payload: SensorPayload):
    """
    Receive a single sensor reading from the simulator.
    Validates the payload and stores it in the aggregation buffer.
    """
    with buffer_lock:
        buffer[payload.bin_id][payload.sensor_type].append({
            "value":     payload.value,
            "alert":     payload.alert,
            "timestamp": payload.timestamp,
        })

    log.info(
        "RECV  bin=%-8s sensor=%-12s value=%-10s alert=%s",
        payload.bin_id,
        payload.sensor_type,
        f"{payload.value} {payload.unit}",
        payload.alert,
    )
    return {"status": "accepted"}


@app.get("/buffer")
def view_buffer():
    """Debug endpoint — see what's currently in the aggregation buffer."""
    with buffer_lock:
        snapshot = {
            bin_id: {
                stype: len(readings)
                for stype, readings in sensors.items()
            }
            for bin_id, sensors in buffer.items()
        }
    return snapshot


# ---------------------------------------------------------------------------
# Aggregation & dispatch (runs in background thread every WINDOW_SECONDS)
# ---------------------------------------------------------------------------

def aggregate_and_dispatch():
    """
    Every WINDOW_SECONDS:
      1. Drain the buffer
      2. Compute average value per sensor, flag any alerts
      3. Build a single compact payload per bin
      4. POST it to the cloud backend
    """
    while True:
        time.sleep(WINDOW_SECONDS)

        with buffer_lock:
            snapshot = dict(buffer)
            buffer.clear()

        if not snapshot:
            log.info("AGGR  (no data this window)")
            continue

        for bin_id, sensors in snapshot.items():
            aggregated = {}

            for sensor_type, readings in sensors.items():
                if not readings:
                    continue

                # For lid_status (boolean) use last known value
                if sensor_type == "lid_status":
                    last = readings[-1]
                    aggregated[sensor_type] = {
                        "value":       last["value"],
                        "alert":       last["alert"],
                        "sample_count": len(readings),
                    }
                else:
                    values = [r["value"] for r in readings]
                    aggregated[sensor_type] = {
                        "value_avg":   round(sum(values) / len(values), 3),
                        "value_min":   round(min(values), 3),
                        "value_max":   round(max(values), 3),
                        "alert":       any(r["alert"] for r in readings),
                        "sample_count": len(readings),
                    }

            cloud_payload = {
                "bin_id":    bin_id,
                "window_s":  WINDOW_SECONDS,
                "dispatched_at": datetime.now(timezone.utc).isoformat(),
                "sensors":   aggregated,
            }

            log.info("AGGR  bin=%s sensors=%s → dispatching to cloud", bin_id, list(aggregated.keys()))
            dispatch_to_cloud(cloud_payload)


def dispatch_to_cloud(payload: dict):
    try:
        resp = requests.post(CLOUD_URL, json=payload, timeout=5)
        resp.raise_for_status()
        log.info("CLOUD ✓ status=%d", resp.status_code)
    except requests.exceptions.ConnectionError:
        log.warning("CLOUD not reachable — payload lost: %s", json.dumps(payload)[:120])
    except requests.exceptions.RequestException as exc:
        log.error("CLOUD dispatch error: %s", exc)


# ---------------------------------------------------------------------------
# Start background aggregation thread on startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def start_aggregator():
    t = threading.Thread(target=aggregate_and_dispatch, daemon=True)
    t.start()
    log.info("Aggregation window = %.0fs | Cloud URL = %s", WINDOW_SECONDS, CLOUD_URL)


# ---------------------------------------------------------------------------
# Run with:  uvicorn fog_node:app --host 0.0.0.0 --port 8000 --reload
# ---------------------------------------------------------------------------
