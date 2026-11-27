# Smart Traffic Management System

An AI-assisted, IoT-based traffic control and monitoring platform. The operator console is named **Gridline**.

**This project uses no physical hardware.** All sensors, vehicles, signals and incidents are software-simulated, and the
interface labels them as simulated. It has never been deployed on a real road. Everything runs locally: no API keys, no
accounts, no paid services, and no internet connection once installed.

The project demonstrates the full IoT lifecycle:

```
SENSOR SIMULATION → MQTT → DATA PROCESSING → INTELLIGENT DECISION → SIGNAL CONTROL → REAL-TIME MONITORING → ANALYTICS
```

## What it does

- Six interconnected intersections, 24 approaches and 126 virtual sensors, with vehicles moving on a live city map.
- Sensor readings travel through a real Eclipse Mosquitto broker. If the broker is down, a labelled local fallback
  takes over and the system reconnects by itself.
- An adaptive algorithm chooses each green from sensor data and explains every decision.
- A safety state machine guarantees yellow, all-red clearance, and minimum and maximum green. No request can bypass it.
- Emergency vehicles get controlled signal priority, and incidents are detected from sensor patterns.
- Machine-learning forecasts are compared against a baseline, with measured error. History, analytics and CSV reports
  survive restarts.

## Quick start with Docker (recommended)

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/), running. In Windows PowerShell:

```powershell
cd E:\IOT-project\smart-traffic-management
docker compose up -d --build
```

Open <http://localhost:5173>. The first start seeds 14 days of history and is ready in about 20 seconds after the images
build. Prediction models finish training about 15 to 30 seconds later.

API documentation (Swagger) is at <http://localhost:8000/docs>.

Stop the project and keep all data:

```powershell
docker compose down
```

Stop and delete all data, for a completely fresh start:

```powershell
docker compose down -v
```

## Run without Docker

You need Python 3.11+ and Node.js 20+. By default this uses a SQLite file, so PostgreSQL is optional.

One-time setup, in PowerShell:

```powershell
cd E:\IOT-project\smart-traffic-management\backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
cd ..\frontend
npm install
```

**Terminal 1, broker (optional).** Use Docker for the broker alone, or install Mosquitto for Windows and run
`mosquitto -c mqtt\mosquitto.conf`. Without a broker the app runs on the labelled local fallback.

```powershell
cd E:\IOT-project\smart-traffic-management
docker compose up -d mosquitto
```

**Terminal 2, backend:**

```powershell
cd E:\IOT-project\smart-traffic-management\backend
.\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

**Terminal 3, frontend:**

```powershell
cd E:\IOT-project\smart-traffic-management\frontend
npm run dev
```

Open <http://localhost:5173>. Stop each terminal with `Ctrl+C`, then run `docker compose down` if you started the broker.

To use local PostgreSQL instead of SQLite, set `DATABASE_URL` before starting the backend:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://traffic:traffic@127.0.0.1:5432/traffic"
```

Other settings are listed in [.env.example](.env.example). None of them are credentials.

## Authentication

Not implemented, so there are no demo credentials. The system is a local demonstration and only binds to `127.0.0.1`.

## Tests

```powershell
cd E:\IOT-project\smart-traffic-management\backend
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\ruff check app tests
```

```powershell
cd E:\IOT-project\smart-traffic-management\frontend
npm test
```

End-to-end tests need the stack running and a one-time browser install:

```powershell
cd E:\IOT-project\smart-traffic-management\frontend
npx playwright install chromium
npm run test:e2e
```

Reproduce every measured result in the project report (about 2 minutes):

```powershell
cd E:\IOT-project\smart-traffic-management\backend
.\.venv\Scripts\python -m app.simulation.benchmark --seeds 5 --minutes 30
```

Run the simulation with no web server, database or broker at all:

```powershell
.\.venv\Scripts\python -m app.simulation.headless --scenario MORNING_RUSH --minutes 30 --compare
```

## Documentation

| Document | Contents |
| --- | --- |
| [docs/demo-guide.md](docs/demo-guide.md) | Step-by-step guide for the college evaluation |
| [docs/project-report.md](docs/project-report.md) | Full project report, including measured results and limitations |
| [docs/architecture.md](docs/architecture.md) | Components, threads and data flow |
| [docs/algorithm-explanation.md](docs/algorithm-explanation.md) | Adaptive control, safety, detection and prediction |
| [docs/mqtt-topics.md](docs/mqtt-topics.md) | Topics and payloads |
| [docs/database-schema.md](docs/database-schema.md) | Tables and relationships |
| [docs/implementation-plan.md](docs/implementation-plan.md) | Plan and key design decisions |

## Project structure

```
smart-traffic-management/
├── backend/
│   ├── app/
│   │   ├── algorithms/   Pure logic: signal FSM, priority, congestion, incidents, preemption
│   │   ├── simulation/   Vehicles, virtual sensors, field controllers, scenarios, benchmark
│   │   ├── mqtt/         Bus, topics, field publisher, backend subscriber
│   │   ├── services/     Processing engine, central controller, persistence, analytics, seed
│   │   ├── ml/           Dataset, training, forecasting
│   │   ├── api/          REST routes
│   │   ├── websocket/    Live stream
│   │   ├── models/       SQLAlchemy models
│   │   └── schemas/      Pydantic schemas, including MQTT payload validation
│   ├── alembic/          Migrations
│   └── tests/
├── frontend/
│   ├── src/              React application
│   └── e2e/              Playwright tests
├── mqtt/mosquitto.conf
├── docs/
└── docker-compose.yml
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| The top bar says "Local fallback" | The broker is not reachable. Start it with `docker compose up -d mosquitto`. The app reconnects by itself. |
| `docker compose` cannot connect | Docker Desktop is not running. Start it and wait until it says it is running. |
| Port 5173, 8000, 1883 or 5432 is in use | Stop whatever is using that port, such as a local PostgreSQL service on 5432. |
| Prediction page says models are training | Wait about 30 seconds after the first start. |
| The dashboard says "Reconnecting" | The backend is restarting or stopped. The page reconnects by itself. |
