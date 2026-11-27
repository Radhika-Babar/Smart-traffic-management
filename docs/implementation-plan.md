# Implementation Plan

Repository state at start: empty folder. Toolchain: Python 3.11, Node 22, Docker Desktop.
Nothing pre-existing had to be preserved.

## Key architectural decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Where safety lives | A **field controller** per intersection (`simulation/field_controller.py`) owns the signal FSM. The central backend only sends *requests*. | Mirrors real cabinets: a lost network or a bad command can never produce a conflicting green. |
| Where intelligence lives | Central backend (`services/central_controller.py`) computes priority scores from **MQTT sensor readings only**. | Forces the full lifecycle through the broker: sensor -> MQTT -> decision -> MQTT -> signal. The algorithm never reads simulator ground truth. |
| Phasing | Split phasing: one approach green at a time. | Single-lane approaches carry left turns, so opposing greens would conflict. It also matches "select the direction with the highest demand", and the safety invariant becomes provable (at most one green). |
| Simulator process | Dedicated thread in the backend process, talking to the processing engine only through the message bus. A headless entry point runs it with no web stack at all. | One command to start on a student laptop, without giving up the MQTT data plane. |
| Fallback | `LocalHub` in-process dispatch when the broker is unreachable, always labelled `LOCAL_FALLBACK` in the API and UI. | Spec requirement. Never presented as MQTT. |
| Clock | All traffic data is stamped with the **simulated city clock**, which starts at wall time and is persisted. | Rates stay correct at 10x and time-of-day features stay meaningful for ML. |
| Persistence | Sync SQLAlchemy in a batching writer thread; REST handlers are plain `def` (threadpool). | Nothing blocks the event loop. Identical code on SQLite and PostgreSQL. |
| Seed history | Mesoscopic queue model driven by the same demand profile as the micro-simulation, labelled `SEEDED`. | Running the micro-simulation for 14 simulated days would take hours. Live rows are labelled `LIVE`. |
| Map | Canvas-rendered local schematic. | No tiles, no keys, no network. 300 moving vehicles stay smooth. |
| Authentication | Not implemented. | The system is a local demo with no external exposure. No secrets are needed. |

## Phases

1. Foundation: structure, config, Docker Compose, Mosquitto config.
2. Backend and database: models, Alembic, REST API, seed data.
3. IoT simulation: vehicles, virtual sensors, MQTT endpoints.
4. Traffic algorithm: FSM, adaptive control, congestion, incidents, emergency preemption, tests.
5. Frontend: layout, dashboard, live map, all twelve sections.
6. Real-time integration: WebSocket stream wired into a shared store.
7. ML and analytics: dataset, model comparison, live forecasts, reports.
8. Testing and polish: end-to-end run through the acceptance criteria, final docs.
