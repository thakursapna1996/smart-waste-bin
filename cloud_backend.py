import json
import os
import threading
import logging
import queue
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CLOUD] [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Simple file-based "database" (swap for CosmosDB on Azure)
# ---------------------------------------------------------------------------

DB_PATH = Path("database.json")
db_lock = threading.Lock()

def db_load() -> list:
    if not DB_PATH.exists():
        return []
    with open(DB_PATH) as f:
        return json.load(f)

def db_save(records: list):
    with open(DB_PATH, "w") as f:
        json.dump(records, f, indent=2)

def db_insert(record: dict):
    with db_lock:
        records = db_load()
        records.append(record)
        # Keep only last 1000 records to avoid bloat
        if len(records) > 1000:
            records = records[-1000:]
        db_save(records)

# ---------------------------------------------------------------------------
# In-memory message queue — simulates AWS SQS
# Fog node pushes here, workers pull and write to DB asynchronously
# This is the scalability pattern (queue + worker pool = FaaS/Lambda simulation)
# ---------------------------------------------------------------------------

message_queue: queue.Queue = queue.Queue()
WORKER_COUNT = 3   # simulates 3 concurrent Lambda function instances

def worker(worker_id: int):
    """
    Background worker thread — simulates an AWS Lambda function
    triggered by an SQS message. Each worker independently pulls
    from the queue and writes to the database.
    """
    log.info("Worker-%d started (simulates AWS Lambda consumer)", worker_id)
    while True:
        try:
            record = message_queue.get(timeout=5)
        except queue.Empty:
            continue
        try:
            db_insert(record)
            log.info(
                "Worker-%d STORED bin=%-8s sensors=%s alerts=%s",
                worker_id,
                record["bin_id"],
                list(record["sensors"].keys()),
                [s for s, d in record["sensors"].items() if d.get("alert")]
            )
        except Exception as exc:
            log.error("Worker-%d error: %s", worker_id, exc)
        finally:
            message_queue.task_done()

# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class FogPayload(BaseModel):
    bin_id:       str
    window_s:     float
    dispatched_at: str
    sensors:      dict

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Smart Waste Bin — Cloud Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Receive endpoint (called by fog node)
# ---------------------------------------------------------------------------

@app.post("/receive", status_code=200)
def receive(payload: FogPayload):
    """
    Receives aggregated sensor data from the fog node.
    Pushes onto the in-memory queue (simulates AWS SQS).
    Worker threads pick it up asynchronously (simulates AWS Lambda).
    """
    record = {
        "bin_id":        payload.bin_id,
        "window_s":      payload.window_s,
        "dispatched_at": payload.dispatched_at,
        "received_at":   datetime.now(timezone.utc).isoformat(),
        "sensors":       payload.sensors,
    }

    # Push to queue — decouples ingestion from storage (scalability pattern)
    message_queue.put(record)
    log.info(
        "QUEUED bin=%-8s sensors=%s queue_size=%d",
        payload.bin_id,
        list(payload.sensors.keys()),
        message_queue.qsize()
    )

    # Log any alerts
    for sensor, data in payload.sensors.items():
        if data.get("alert"):
            log.warning("ALERT  bin=%s sensor=%s value=%s",
                        payload.bin_id, sensor,
                        data.get("value") or data.get("value_avg"))

    return {
        "status":     "queued",
        "bin_id":     payload.bin_id,
        "queue_size": message_queue.qsize()
    }

@app.get("/", response_class=HTMLResponse)
def dashboard_page():
    """
    Serves the full dashboard when someone opens the AWS URL.
    Examiner just opens the URL and sees live charts immediately.
    """
    AWS_URL = "http://Smart-waste-bin-env.eba-zvrsdqbr.us-east-1.elasticbeanstalk.com"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Smart Waste Bin Dashboard</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f0f2f5;color:#1a1a2e;min-height:100vh}}
    header{{background:#1a1a2e;color:white;padding:.9rem 1.5rem;display:flex;align-items:center;justify-content:space-between}}
    header h1{{font-size:1rem;font-weight:600;display:flex;align-items:center;gap:8px}}
    #dot{{width:9px;height:9px;border-radius:50%;background:#888;display:inline-block}}
    #dot.live{{background:#4ade80;animation:pulse 2s infinite}}
    @keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}
    #upd{{font-size:11px;color:#9ca3af}}
    main{{max-width:1200px;margin:0 auto;padding:1.25rem}}
    .sec{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:#6b7280;margin:1.25rem 0 .6rem}}
    .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:1rem}}
    .card{{background:white;border-radius:12px;padding:1rem;border:1.5px solid #e5e7eb}}
    .card-label{{font-size:11px;font-weight:600;text-transform:uppercase;color:#888;margin-bottom:6px}}
    .card-value{{font-size:26px;font-weight:700;color:#1a1a2e;line-height:1.1}}
    .card-unit{{font-size:12px;color:#aaa;margin-top:2px}}
    .card.alert-active{{border-color:#f87171;background:#fff5f5}}
    .card.alert-active .card-value{{color:#dc2626}}
    .alert-badge{{display:inline-block;font-size:10px;font-weight:600;background:#fee2e2;color:#dc2626;padding:2px 7px;border-radius:20px;margin-top:4px}}
    .charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(480px,1fr));gap:16px}}
    .chart-card{{background:white;border-radius:12px;padding:1.25rem;border:1px solid #e8eaf0}}
    .chart-title{{font-size:13px;font-weight:600;color:#555;margin-bottom:.75rem;text-transform:uppercase;letter-spacing:.04em}}
    .chart-wrap{{position:relative;height:200px}}
    .alerts-card{{background:white;border-radius:12px;padding:1.25rem;border:1px solid #e8eaf0;margin-top:16px}}
    .alerts-title{{font-size:13px;font-weight:600;color:#555;text-transform:uppercase;letter-spacing:.04em;margin-bottom:.75rem}}
    .alert-row{{display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid #f0f0f0;font-size:13px}}
    .alert-row:last-child{{border-bottom:none}}
    .alert-time{{color:#999;min-width:160px}}
    .alert-sensor{{font-weight:600;min-width:120px;text-transform:capitalize}}
    .alert-val{{color:#dc2626;font-weight:500}}
    .no-alerts{{color:#aaa;font-size:13px}}
    .toolbar{{display:flex;align-items:center;gap:12px;margin-bottom:1.25rem}}
    .toolbar label{{font-size:13px;color:#666;font-weight:500}}
    .toolbar select{{padding:6px 10px;border-radius:8px;border:1px solid #ddd;font-size:13px;background:white}}
    .refresh-btn{{margin-left:auto;padding:6px 14px;border-radius:8px;border:1px solid #ddd;background:white;font-size:13px;cursor:pointer;font-weight:500}}
    .refresh-btn:hover{{background:#f0f0f0}}
    .aws-badge{{background:#ff9900;color:white;font-size:11px;font-weight:700;padding:3px 10px;border-radius:20px}}
  </style>
</head>
<body>
<header>
  <h1><span id="dot"></span> Smart Waste Bin — Live Dashboard</h1>
  <div style="display:flex;align-items:center;gap:12px">
    <span class="aws-badge">AWS Elastic Beanstalk</span>
    <span id="upd">Connecting...</span>
  </div>
</header>
<main>
  <div class="toolbar">
    <label>Bin:</label>
    <select id="bin-select" onchange="onBinChange()"><option value="">Loading...</option></select>
    <button class="refresh-btn" onclick="refresh()">Refresh</button>
  </div>
  <div class="sec">Sensor readings</div>
  <div class="cards">
    <div class="card" id="card-fill_level"><div class="card-label">Fill Level</div><div class="card-value" id="val-fill_level">—</div><div class="card-unit">percent</div></div>
    <div class="card" id="card-weight"><div class="card-label">Weight</div><div class="card-value" id="val-weight">—</div><div class="card-unit">kg</div></div>
    <div class="card" id="card-gas_odour"><div class="card-label">Gas / Odour</div><div class="card-value" id="val-gas_odour">—</div><div class="card-unit">ppm</div></div>
    <div class="card" id="card-temperature"><div class="card-label">Temperature</div><div class="card-value" id="val-temperature">—</div><div class="card-unit">°C</div></div>
    <div class="card" id="card-lid_status"><div class="card-label">Lid Status</div><div class="card-value" id="val-lid_status">—</div><div class="card-unit">open / closed</div></div>
  </div>
  <div class="sec">Trends</div>
  <div class="charts">
    <div class="chart-card"><div class="chart-title">Fill Level (%)</div><div class="chart-wrap"><canvas id="chart-fill_level"></canvas></div></div>
    <div class="chart-card"><div class="chart-title">Weight (kg)</div><div class="chart-wrap"><canvas id="chart-weight"></canvas></div></div>
    <div class="chart-card"><div class="chart-title">Gas / Odour (ppm)</div><div class="chart-wrap"><canvas id="chart-gas_odour"></canvas></div></div>
    <div class="chart-card"><div class="chart-title">Temperature (°C)</div><div class="chart-wrap"><canvas id="chart-temperature"></canvas></div></div>
  </div>
  <div class="alerts-card">
    <div class="alerts-title">Recent Alerts</div>
    <div id="alerts-list"><p class="no-alerts">No alerts yet.</p></div>
  </div>
</main>
<script>
  const API = "{AWS_URL}";
  let currentBin = "BIN-001";
  let charts = {{}};
  const SENSOR_COLORS = {{
    fill_level:  {{ border:"#6366f1", background:"rgba(99,102,241,0.1)" }},
    weight:      {{ border:"#0ea5e9", background:"rgba(14,165,233,0.1)" }},
    gas_odour:   {{ border:"#f59e0b", background:"rgba(245,158,11,0.1)" }},
    temperature: {{ border:"#f43f5e", background:"rgba(244,63,94,0.1)" }},
  }};
  async function init() {{
    await loadBins();
    await refresh();
    setInterval(refresh, 5000);
  }}
  async function loadBins() {{
    try {{
      const res  = await fetch(`${{API}}/api/bins`);
      const data = await res.json();
      const sel  = document.getElementById("bin-select");
      sel.innerHTML = "";
      if (!data.bins.length) {{ sel.innerHTML = '<option value="">No data yet</option>'; return; }}
      data.bins.forEach(b => {{
        const opt = document.createElement("option");
        opt.value = b; opt.textContent = b;
        if (b === currentBin) opt.selected = true;
        sel.appendChild(opt);
      }});
      currentBin = sel.value;
    }} catch(e) {{
      document.getElementById("bin-select").innerHTML = '<option value="BIN-001">BIN-001</option>';
    }}
  }}
  function onBinChange() {{ currentBin = document.getElementById("bin-select").value; refresh(); }}
  async function refresh() {{
    if (!currentBin) return;
    try {{
      await Promise.all([updateCards(), updateCharts(), updateAlerts()]);
      document.getElementById("dot").className = "live";
      document.getElementById("upd").textContent = "Updated " + new Date().toLocaleTimeString();
    }} catch(e) {{
      document.getElementById("dot").className = "";
      document.getElementById("upd").textContent = "Cannot reach backend";
    }}
  }}
  async function updateCards() {{
    const res  = await fetch(`${{API}}/api/summary/${{currentBin}}`);
    const data = await res.json();
    const s    = data.summary || {{}};
    ["fill_level","weight","gas_odour","temperature","lid_status"].forEach(key => {{
      const info  = s[key];
      const card  = document.getElementById(`card-${{key}}`);
      const valEl = document.getElementById(`val-${{key}}`);
      card.querySelectorAll(".alert-badge").forEach(el => el.remove());
      card.classList.remove("alert-active");
      if (!info) {{ valEl.textContent = "—"; return; }}
      let display = info.latest_value;
      if (key === "lid_status") display = info.latest_value ? "OPEN" : "CLOSED";
      else if (typeof display === "number") display = display.toFixed(1);
      valEl.textContent = display;
      if (info.alert) {{
        card.classList.add("alert-active");
        const b = document.createElement("div");
        b.className = "alert-badge"; b.textContent = "ALERT";
        card.appendChild(b);
      }}
    }});
  }}
  async function updateCharts() {{
    await Promise.all(["fill_level","weight","gas_odour","temperature"].map(async sensor => {{
      const res  = await fetch(`${{API}}/api/history/${{currentBin}}/${{sensor}}?limit=30`);
      const data = await res.json();
      const labels = data.data.map(p => new Date(p.timestamp).toLocaleTimeString([],{{hour:"2-digit",minute:"2-digit",second:"2-digit"}}));
      const values = data.data.map(p => p.value_avg !== null ? parseFloat(p.value_avg.toFixed(2)) : null);
      const colors = SENSOR_COLORS[sensor];
      if (charts[sensor]) {{
        charts[sensor].data.labels = labels;
        charts[sensor].data.datasets[0].data = values;
        charts[sensor].update("none");
      }} else {{
        const ctx = document.getElementById(`chart-${{sensor}}`).getContext("2d");
        charts[sensor] = new Chart(ctx, {{
          type:"line",
          data:{{ labels, datasets:[{{ data:values, borderColor:colors.border, backgroundColor:colors.background, borderWidth:2, pointRadius:3, fill:true, tension:0.3 }}] }},
          options:{{ responsive:true, maintainAspectRatio:false, plugins:{{ legend:{{ display:false }} }}, scales:{{ x:{{ ticks:{{ font:{{ size:10 }}, maxTicksLimit:6 }}, grid:{{ color:"#f0f0f0" }} }}, y:{{ ticks:{{ font:{{ size:11 }} }}, grid:{{ color:"#f0f0f0" }} }} }} }}
        }});
      }}
    }}));
  }}
  async function updateAlerts() {{
    const res  = await fetch(`${{API}}/api/alerts/${{currentBin}}?limit=15`);
    const data = await res.json();
    const list = document.getElementById("alerts-list");
    if (!data.alerts || !data.alerts.length) {{ list.innerHTML = '<p class="no-alerts">No alerts yet.</p>'; return; }}
    list.innerHTML = [...data.alerts].reverse().map(a => {{
      const t   = new Date(a.timestamp).toLocaleString();
      const s   = a.sensor_type.replace(/_/g," ");
      const val = a.value !== null ? parseFloat(a.value).toFixed(1) : "—";
      return `<div class="alert-row"><span class="alert-time">${{t}}</span><span class="alert-sensor">${{s}}</span><span class="alert-val">${{val}}</span></div>`;
    }}).join("");
  }}
  init();
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------

@app.get("/health")
@app.get("/api/health")
def health():
    records = db_load()
    return {
        "status":        "ok",
        "total_records": len(records),
        "queue_size":    message_queue.qsize(),
        "workers":       WORKER_COUNT,
        "time":          datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/bins")
def list_bins():
    """Return list of all bin IDs seen."""
    records = db_load()
    bins = list({r["bin_id"] for r in records})
    return {"bins": bins}


@app.get("/api/latest/{bin_id}")
def latest(bin_id: str):
    """Most recent reading for each sensor for a given bin."""
    records = db_load()
    bin_records = [r for r in records if r["bin_id"] == bin_id]
    if not bin_records:
        return {"bin_id": bin_id, "data": None}

    latest_record = bin_records[-1]
    return {
        "bin_id":        bin_id,
        "dispatched_at": latest_record["dispatched_at"],
        "sensors":       latest_record["sensors"],
    }


@app.get("/api/history/{bin_id}/{sensor_type}")
def history(
    bin_id:      str,
    sensor_type: str,
    limit:       int = Query(default=50, le=200),
):
    """
    Time-series history for a specific bin + sensor.
    Returns up to `limit` most recent data points.
    Used by the dashboard charts.
    """
    records = db_load()
    bin_records = [r for r in records if r["bin_id"] == bin_id]

    points = []
    for r in bin_records:
        if sensor_type in r["sensors"]:
            sensor_data = r["sensors"][sensor_type]
            points.append({
                "timestamp": r["dispatched_at"],
                "value_avg": sensor_data.get("value_avg") or sensor_data.get("value"),
                "value_min": sensor_data.get("value_min"),
                "value_max": sensor_data.get("value_max"),
                "alert":     sensor_data.get("alert", False),
                "samples":   sensor_data.get("sample_count", 1),
            })

    # Return most recent N points
    return {
        "bin_id":      bin_id,
        "sensor_type": sensor_type,
        "count":       len(points[-limit:]),
        "data":        points[-limit:],
    }


@app.get("/api/alerts/{bin_id}")
def alerts(bin_id: str, limit: int = Query(default=20, le=100)):
    """Return recent alert events for a bin."""
    records = db_load()
    alert_events = []

    for r in records:
        if r["bin_id"] != bin_id:
            continue
        for sensor, data in r["sensors"].items():
            if data.get("alert"):
                alert_events.append({
                    "timestamp":   r["dispatched_at"],
                    "sensor_type": sensor,
                    "value":       data.get("value_avg") or data.get("value"),
                })

    return {
        "bin_id": bin_id,
        "alerts": alert_events[-limit:],
    }


@app.get("/api/summary/{bin_id}")
def summary(bin_id: str):
    """
    Dashboard summary card data — latest value + alert status per sensor.
    """
    records = db_load()
    bin_records = [r for r in records if r["bin_id"] == bin_id]
    if not bin_records:
        return {"bin_id": bin_id, "summary": {}}

    latest_record = bin_records[-1]
    summary_data = {}

    for sensor, data in latest_record["sensors"].items():
        summary_data[sensor] = {
            "latest_value": data.get("value_avg") or data.get("value"),
            "alert":        data.get("alert", False),
            "unit": {
                "fill_level":  "percent",
                "weight":      "kg",
                "gas_odour":   "ppm",
                "temperature": "celsius",
                "lid_status":  "open/closed",
            }.get(sensor, ""),
        }

    return {
        "bin_id":        bin_id,
        "last_updated":  latest_record["dispatched_at"],
        "summary":       summary_data,
    }


# ---------------------------------------------------------------------------
# Start worker threads on startup — simulates AWS Lambda consumers
# ---------------------------------------------------------------------------

@app.on_event("startup")
def start_workers():
    for i in range(1, WORKER_COUNT + 1):
        t = threading.Thread(target=worker, args=(i,), daemon=True)
        t.start()
    log.info(
        "%d worker threads started — simulating %d Lambda consumers on SQS queue",
        WORKER_COUNT, WORKER_COUNT
    )

# ---------------------------------------------------------------------------
# Run: uvicorn cloud_backend:app --host 0.0.0.0 --port 9000 --reload
# ---------------------------------------------------------------------------
