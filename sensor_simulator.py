import time
import random
import json
import requests
import os
import logging
from datetime import datetime, timezone
from dotenv import dotenv_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load config from .env (falls back to defaults if not found)
# ---------------------------------------------------------------------------
cfg = dotenv_values(".env")

FOG_NODE_URL   = cfg.get("FOG_NODE_URL",   "http://localhost:8000/ingest")
BIN_ID         = cfg.get("BIN_ID",          "BIN-001")

# Dispatch intervals in seconds (configurable per sensor)
FREQ_FILL      = float(cfg.get("FREQ_FILL",      "5"))
FREQ_WEIGHT    = float(cfg.get("FREQ_WEIGHT",     "5"))
FREQ_GAS       = float(cfg.get("FREQ_GAS",        "3"))
FREQ_TEMP      = float(cfg.get("FREQ_TEMP",       "10"))
FREQ_LID       = float(cfg.get("FREQ_LID",        "2"))


# ---------------------------------------------------------------------------
# Sensor state (simulates a real bin gradually filling up)
# ---------------------------------------------------------------------------
class BinState:
    def __init__(self):
        self.fill_level   = random.uniform(10, 40)   # %
        self.weight       = random.uniform(1, 15)    # kg
        self.lid_open     = False
        self.lid_open_at  = None

    def tick(self):
        """Slowly increase fill & weight over time to simulate a real bin."""
        self.fill_level = min(100, self.fill_level + random.uniform(0, 0.3))
        self.weight     = min(50,  self.weight     + random.uniform(0, 0.1))

        # Lid randomly opens for a short burst then closes
        if not self.lid_open and random.random() < 0.04:
            self.lid_open    = True
            self.lid_open_at = time.time()
        if self.lid_open and (time.time() - self.lid_open_at) > random.uniform(2, 6):
            self.lid_open = False


state = BinState()


# ---------------------------------------------------------------------------
# Individual sensor reading functions
# ---------------------------------------------------------------------------

def read_fill_level() -> dict:
    state.tick()
    level = round(state.fill_level + random.uniform(-0.5, 0.5), 2)
    return {
        "sensor_type": "fill_level",
        "unit":        "percent",
        "value":       max(0, min(100, level)),
        "alert":       level >= 80,
    }


def read_weight() -> dict:
    w = round(state.weight + random.uniform(-0.2, 0.2), 3)
    return {
        "sensor_type": "weight",
        "unit":        "kg",
        "value":       max(0, w),
        "alert":       w >= 40,
    }


def read_gas() -> dict:
    # Gas spikes when lid is open or bin is very full
    base = 50 + (state.fill_level * 1.5)
    spike = random.uniform(200, 600) if state.lid_open else 0
    ppm = round(base + spike + random.uniform(-10, 10), 1)
    return {
        "sensor_type": "gas_odour",
        "unit":        "ppm",
        "value":       max(0, ppm),
        "alert":       ppm >= 400,
    }


def read_temperature() -> dict:
    # Warmer when more organic waste / lid has been open
    base = 18 + (state.fill_level * 0.12)
    temp = round(base + random.uniform(-1.5, 1.5), 2)
    return {
        "sensor_type": "temperature",
        "unit":        "celsius",
        "value":       temp,
        "alert":       temp >= 35,
    }


def read_lid_status() -> dict:
    return {
        "sensor_type": "lid_status",
        "unit":        "boolean",
        "value":       state.lid_open,
        "alert":       state.lid_open,
    }


# ---------------------------------------------------------------------------
# Dispatch helper
# ---------------------------------------------------------------------------

def build_payload(reading: dict) -> dict:
    return {
        "bin_id":    BIN_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **reading,
    }


def dispatch(reading: dict):
    payload = build_payload(reading)
    try:
        resp = requests.post(FOG_NODE_URL, json=payload, timeout=5)
        resp.raise_for_status()
        log.info(
            "Sent %-12s | value=%-10s | alert=%s | status=%d",
            reading["sensor_type"],
            f"{reading['value']} {reading['unit']}",
            reading["alert"],
            resp.status_code,
        )
    except requests.exceptions.ConnectionError:
        log.warning("Fog node not reachable — payload queued locally: %s", json.dumps(payload))
    except requests.exceptions.RequestException as exc:
        log.error("Dispatch error: %s", exc)


# ---------------------------------------------------------------------------
# Per-sensor loop (runs in its own thread)
# ---------------------------------------------------------------------------

import threading

def sensor_loop(read_fn, interval: float, name: str):
    log.info("Starting %s sensor (every %.1fs)", name, interval)
    while True:
        dispatch(read_fn())
        time.sleep(interval)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("=== Smart Waste Bin Sensor Simulator ===")
    log.info("Bin ID       : %s", BIN_ID)
    log.info("Fog Node URL : %s", FOG_NODE_URL)
    log.info("")

    sensors = [
        (read_fill_level,   FREQ_FILL,   "fill_level"),
        (read_weight,       FREQ_WEIGHT, "weight"),
        (read_gas,          FREQ_GAS,    "gas_odour"),
        (read_temperature,  FREQ_TEMP,   "temperature"),
        (read_lid_status,   FREQ_LID,    "lid_status"),
    ]

    threads = []
    for fn, freq, name in sensors:
        t = threading.Thread(target=sensor_loop, args=(fn, freq, name), daemon=True)
        t.start()
        threads.append(t)

    log.info("All %d sensors running. Press Ctrl+C to stop.\n", len(sensors))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Simulator stopped.")
