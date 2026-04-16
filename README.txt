================================================================
 SMART WASTE BIN MONITORING SYSTEM
 Fog and Edge Computing Module — NCI Dublin 2026
================================================================

LIVE AWS URL:
  http://Smart-waste-bin-env.eba-zvrsdqbr.us-east-1.elasticbeanstalk.com

================================================================
 SYSTEM REQUIREMENTS
================================================================
- Python 3.11 or higher
- pip (Python package manager)
- A terminal / VS Code

================================================================
 PROJECT STRUCTURE
================================================================
Smart waste bin/
├── cloud_backend.py       Cloud backend (deployed on AWS)
├── fog_node.py            Virtual fog node
├── sensor_simulator.py    Mock sensor data generator
├── index.html             Local dashboard (optional)
├── requirements.txt       Python dependencies
├── database.json          Local data storage
├── fog_node/
│   └── .env               Fog node config (CLOUD_URL etc.)
└── sensors/
    └── .env               Sensor config (frequencies etc.)

================================================================
 INSTALLATION
================================================================

Step 1 — Install dependencies:
  pip install fastapi==0.111.0 uvicorn[standard]==0.29.0 pydantic==1.10.17 python-dotenv==1.0.1 requests==2.31.0 gunicorn==21.2.0

Step 2 — Configure fog node:
  Open fog_node/.env and set:
  CLOUD_URL=http://Smart-waste-bin-env.eba-zvrsdqbr.us-east-1.elasticbeanstalk.com/receive
  WINDOW_SECONDS=10

Step 3 — Configure sensors:
  Open sensors/.env and set:
  FOG_NODE_URL=http://localhost:8000/ingest
  BIN_ID=BIN-001
  FREQ_FILL=5
  FREQ_WEIGHT=5
  FREQ_GAS=3
  FREQ_TEMP=10
  FREQ_LID=2

================================================================
 HOW TO RUN
================================================================

Open 2 terminal tabs in VS Code:

TAB 1 — Start the fog node:
  cd "Smart waste bin"
  uvicorn fog_node:app --host 0.0.0.0 --port 8000 --reload

  Wait until you see:
  "Aggregation window = 10s | Cloud URL = http://Smart-waste-bin..."

TAB 2 — Start the sensor simulator:
  cd "Smart waste bin"
  python sensor_simulator.py

  Wait until you see:
  "Sent fill_level | value=xx percent | status=202"

================================================================
 VERIFY IT IS WORKING
================================================================

1. Fog node receiving data:
   http://localhost:8000/health

2. Data reaching AWS (total_records should increase):
   http://Smart-waste-bin-env.eba-zvrsdqbr.us-east-1.elasticbeanstalk.com/api/health

3. Live dashboard on AWS:
   http://Smart-waste-bin-env.eba-zvrsdqbr.us-east-1.elasticbeanstalk.com

4. API documentation:
   http://Smart-waste-bin-env.eba-zvrsdqbr.us-east-1.elasticbeanstalk.com/docs

================================================================
 SENSOR TYPES
================================================================
1. Fill Level    — Ultrasonic sensor (0-100%)    every 5s
2. Weight        — Load cell (0-50 kg)           every 5s
3. Gas / Odour   — MQ sensor (ppm)               every 3s
4. Temperature   — DHT22 (Celsius)               every 10s
5. Lid Status    — Reed switch (open/closed)      every 2s

================================================================
 ARCHITECTURE
================================================================
Sensors (Mac) → Fog Node (Mac) → AWS Elastic Beanstalk
                                        ↓
                              Queue (simulates SQS)
                                        ↓
                           3 Workers (simulate Lambda)
                                        ↓
                              Database (JSON file)
                                        ↓
                              Live Dashboard (browser)

================================================================
 TROUBLESHOOTING
================================================================

Problem: "CLOUD not reachable" in fog node terminal
Fix: Check fog_node/.env has AWS URL not localhost

Problem: Dashboard shows no data
Fix: Restart fog node and sensor simulator

Problem: AWS URL not loading
Fix: Switch from college wifi to mobile hotspot

Problem: Port already in use
Fix: Run: lsof -i :8000 then kill -9 <PID>

================================================================
 STOPPING THE APP
================================================================
Press Ctrl+C in each terminal tab.

================================================================
