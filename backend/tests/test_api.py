"""API tests against a running application with a real database and simulator."""

import time

import pytest


def advance(client, seconds: float) -> None:
    """Step the simulator deterministically, then let the pipeline drain."""
    runtime = client.runtime

    def drain() -> None:
        deadline = time.time() + 5
        while time.time() < deadline and (runtime.subscriber.backlog or runtime.hub._queue.qsize()):
            time.sleep(0.001)

    for tick in range(round(seconds / runtime.engine.dt)):
        runtime.engine.step()
        if tick % 2:
            # Real pacing leaves the bus time to deliver commands between ticks.
            drain()
    drain()
    time.sleep(0.05)
    runtime.writer.flush()


class TestSeed:
    def test_six_intersections_with_four_approaches(self, client) -> None:
        rows = client.get("/api/intersections").json()
        assert len(rows) >= 6
        first = rows[0]
        assert first["id"] == "INT-001" and len(first["approaches"]) == 4
        assert first["sensor_count"] == 21

    def test_sensors_and_signals_seeded(self, client) -> None:
        assert len(client.get("/api/sensors").json()) >= 24
        assert len(client.get("/api/signals").json()) >= 6

    def test_seeded_alerts_are_labelled(self, client) -> None:
        items = client.get("/api/alerts", params={"resolved": True}).json()["items"]
        assert any(a["source"] == "SEEDED" for a in items)


class TestErrors:
    def test_missing_resource_has_consistent_shape(self, client) -> None:
        response = client.get("/api/intersections/INT-999")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_validation_error_shape(self, client) -> None:
        response = client.post("/api/simulation/speed", json={"speed": 3})
        assert response.status_code == 422
        body = response.json()["error"]
        assert body["code"] == "validation_error" and body["details"]

    def test_docs_are_served(self, client) -> None:
        assert client.get("/openapi.json").status_code == 200


class TestSimulationControl:
    def test_start_pause_resume_cycle_is_idempotent(self, client) -> None:
        assert client.post("/api/simulation/start").json()["state"] == "RUNNING"
        assert client.post("/api/simulation/start").json()["state"] == "RUNNING"
        thread = client.runtime.runner._thread
        assert client.post("/api/simulation/pause").json()["state"] == "PAUSED"
        assert client.post("/api/simulation/resume").json()["state"] == "RUNNING"
        assert client.runtime.runner._thread is thread, "no duplicate worker"
        assert client.post("/api/simulation/pause").json()["state"] == "PAUSED"

    def test_paused_simulation_does_not_advance(self, client) -> None:
        before = client.get("/api/simulation/status").json()["sim_seconds"]
        time.sleep(0.3)
        assert client.get("/api/simulation/status").json()["sim_seconds"] == before

    @pytest.mark.parametrize("speed", [1, 2, 5, 10])
    def test_speed(self, client, speed) -> None:
        assert client.post("/api/simulation/speed", json={"speed": speed}).json()["speed"] == speed

    def test_all_scenarios_listed_and_apply(self, client) -> None:
        scenarios = client.get("/api/simulation/scenarios").json()
        assert len(scenarios) == 8
        for scenario in scenarios:
            body = client.post("/api/simulation/scenario", json={"key": scenario["key"]}).json()
            assert body["scenario"] == scenario["key"]
        client.post("/api/simulation/reset")

    def test_random_scenario(self, client) -> None:
        assert client.post("/api/simulation/scenario", json={"key": "RANDOM"}).status_code == 200
        client.post("/api/simulation/reset")

    def test_unknown_scenario(self, client) -> None:
        assert client.post("/api/simulation/scenario", json={"key": "NOPE"}).status_code == 404

    def test_parameters_change_engine(self, client) -> None:
        body = client.post("/api/simulation/parameters", json={"volume_factor": 1.5, "spawn_rate_vph": 500}).json()
        assert body["volume_factor"] == 1.5 and body["spawn_rate_vph"] == 500
        body = client.post("/api/simulation/parameters", json={"volume_factor": 1, "clear_spawn_rate": True}).json()
        assert body["spawn_rate_vph"] is None

    def test_weather(self, client) -> None:
        assert client.post("/api/simulation/weather", json={"condition": "RAIN"}).json()["weather"] == "RAIN"
        client.post("/api/simulation/weather", json={"condition": "CLEAR"})


class TestLiveData:
    def test_dashboard_reflects_simulation(self, client) -> None:
        client.post("/api/simulation/scenario", json={"key": "MORNING_RUSH"})
        advance(client, 200)
        overview = client.get("/api/dashboard/overview").json()
        assert overview["total_intersections"] >= 6
        assert overview["total_vehicles"] > 10
        assert overview["average_density"] > 0
        assert overview["active_sensors"] >= 24
        assert overview["network_health"]["transport"] == "LOCAL_FALLBACK"
        assert overview["simulated"] is True

    def test_intersection_traffic_has_explanation(self, client) -> None:
        view = client.get("/api/intersections/INT-001/traffic").json()
        assert view["phase"] in ("GREEN", "YELLOW", "ALL_RED", "RED")
        assert len(view["approaches"]) == 4 and len(view["scores"]) == 4

    def test_sensor_readings_are_persisted(self, client) -> None:
        sensors = client.get("/api/sensors", params={"sensor_type": "TRAFFIC_DENSITY"}).json()
        target = sensors[0]
        assert target["latest"]
        readings = client.get(f"/api/sensors/{target['id']}/readings").json()
        assert readings and readings[-1]["traffic_density"] is not None

    def test_decisions_and_history_recorded(self, client) -> None:
        assert client.get("/api/signals/SIG-001/decisions").json()
        assert client.get("/api/signals/SIG-001/history").json()

    def test_topology_for_the_map(self, client) -> None:
        topology = client.get("/api/dashboard/topology").json()
        assert len(topology["intersections"]) >= 6 and len(topology["links"]) >= 24


class TestSignals:
    def test_manual_control_requires_manual_mode(self, client) -> None:
        response = client.post("/api/signals/SIG-002/manual-control", json={"direction": "NORTH"})
        assert response.status_code == 409

    def test_manual_flow_through_controller(self, client) -> None:
        assert client.post("/api/signals/SIG-002/mode", json={"mode": "MANUAL"}).json()["mode"] == "MANUAL"
        advance(client, 3)
        controller = client.runtime.engine.controllers["INT-002"]
        target = next(d for d in ("NORTH", "EAST", "SOUTH", "WEST") if d != controller.fsm.active)
        assert client.post("/api/signals/SIG-002/manual-control", json={"direction": target}).status_code == 202
        advance(client, 30)
        assert controller.fsm.active == target
        client.post("/api/signals/SIG-002/mode", json={"mode": "AUTOMATIC"})
        advance(client, 2)
        assert controller.fsm.mode.value == "AUTOMATIC"

    def test_timing_update_reaches_controller(self, client) -> None:
        body = {
            "min_green_s": 10, "max_green_s": 50, "default_green_s": 20, "yellow_s": 4, "all_red_s": 3,
            "preempt_max_hold_s": 40,
        }
        assert client.put("/api/signals/SIG-003/timing", json=body).status_code == 200
        assert client.runtime.engine.controllers["INT-003"].fsm.timing.yellow == 4

    @pytest.mark.parametrize(
        "patch", [{"min_green_s": 60, "max_green_s": 30}, {"yellow_s": 1}, {"all_red_s": 0}, {"default_green_s": 99}]
    )
    def test_unsafe_timing_rejected(self, client, patch) -> None:
        body = {
            "min_green_s": 10, "max_green_s": 50, "default_green_s": 20, "yellow_s": 3, "all_red_s": 2, **patch,
        }
        assert client.put("/api/signals/SIG-003/timing", json=body).status_code == 422


class TestSensors:
    def test_failure_then_restart_via_api(self, client) -> None:
        failed = client.post(
            "/api/simulation/sensor-failure", json={"intersection_id": "INT-004", "count": 1, "duration_s": 500}
        ).json()["failed"][0]
        advance(client, 40)
        assert client.get(f"/api/sensors/{failed}").json()["status"] == "OFFLINE"
        alerts = client.get("/api/alerts", params={"category": "SENSOR_FAILURE", "resolved": False}).json()["items"]
        assert any(failed in a["description"] for a in alerts)
        assert client.post(f"/api/sensors/{failed}/restart").status_code == 200
        advance(client, 40)
        assert client.get(f"/api/sensors/{failed}").json()["status"] == "ONLINE"

    def test_disable_enable_and_interval(self, client) -> None:
        sensor = client.get("/api/sensors", params={"intersection_id": "INT-005"}).json()[0]["id"]
        assert client.patch(f"/api/sensors/{sensor}", json={"enabled": False}).json()["status"] == "DISABLED"
        assert client.post(f"/api/sensors/{sensor}/restart").status_code == 409
        assert client.patch(f"/api/sensors/{sensor}", json={"enabled": True}).json()["status"] == "ONLINE"
        assert client.patch(f"/api/sensors/{sensor}", json={"reporting_interval_s": 9}).json()["reporting_interval_s"] == 9
        assert client.patch(f"/api/sensors/{sensor}", json={"reporting_interval_s": 0}).status_code == 422

    def test_register_sensor_starts_reporting(self, client) -> None:
        created = client.post("/api/sensors", json={
            "sensor_type": "AVERAGE_SPEED", "intersection_id": "INT-001", "direction": "NORTH",
            "reporting_interval_s": 2,
        })
        assert created.status_code == 201
        sensor_id = created.json()["id"]
        advance(client, 6)
        assert client.get(f"/api/sensors/{sensor_id}").json()["readings_received"] >= 1

    def test_road_sensor_needs_direction(self, client) -> None:
        response = client.post("/api/sensors", json={"sensor_type": "AVERAGE_SPEED", "intersection_id": "INT-001"})
        assert response.status_code == 422


class TestEmergencyAndIncidents:
    def test_dispatch_lifecycle(self, client) -> None:
        route = client.get("/api/emergency-vehicles/route", params={"origin": "INT-001", "destination": "INT-003"})
        assert route.json()["route"] == ["INT-001", "INT-002", "INT-003"]
        created = client.post("/api/emergency-vehicles", json={
            "vehicle_type": "FIRE_ENGINE", "origin": "INT-001", "destination": "INT-003",
        })
        assert created.status_code == 201
        vehicle_id = created.json()["vehicle_id"]
        advance(client, 180)
        records = {r["id"]: r for r in client.get("/api/emergency-vehicles").json()}
        assert records[vehicle_id]["status"] == "ARRIVED"
        assert records[vehicle_id]["preemptions"] >= 1
        assert records[vehicle_id]["travel_time_s"] > 0

    def test_reset_closes_vehicles_still_on_the_road(self, client) -> None:
        vehicle_id = client.post("/api/emergency-vehicles", json={
            "vehicle_type": "POLICE", "origin": "INT-001", "destination": "INT-003",
        }).json()["vehicle_id"]
        advance(client, 3)
        client.post("/api/simulation/reset")
        records = {r["id"]: r for r in client.get("/api/emergency-vehicles").json()}
        assert records[vehicle_id]["status"] == "CANCELLED"

    def test_invalid_dispatch(self, client) -> None:
        body = {"vehicle_type": "AMBULANCE", "origin": "INT-001", "destination": "INT-001"}
        assert client.post("/api/emergency-vehicles", json=body).status_code == 422
        assert client.delete("/api/emergency-vehicles/AMB-0").status_code == 404

    def test_incident_labelled_and_alerts(self, client) -> None:
        created = client.post("/api/incidents/simulate", json={
            "incident_type": "MAJOR_ACCIDENT", "intersection_id": "INT-002", "direction": "NORTH", "duration_s": 200,
        })
        assert created.status_code == 201
        incident_id = created.json()["incident_id"]
        advance(client, 5)
        rows = {r["id"]: r for r in client.get("/api/incidents").json()}
        assert rows[incident_id]["origin"] == "INJECTED" and rows[incident_id]["simulated"] is True
        alerts = client.get("/api/alerts", params={"category": "SIMULATED_INCIDENT"}).json()["items"]
        assert alerts and "Simulated" in alerts[0]["description"]
        assert client.post(f"/api/incidents/{incident_id}/resolve").status_code == 200
        advance(client, 2)
        assert client.get("/api/incidents").json()[0]["status"] == "RESOLVED"
        assert client.post(f"/api/incidents/{incident_id}/resolve").status_code == 404


class TestAlerts:
    def test_acknowledge_and_filter(self, client) -> None:
        alert_id = client.get("/api/alerts").json()["items"][0]["id"]
        assert client.post(f"/api/alerts/{alert_id}/acknowledge").json()["acknowledged"] is True
        unacked = client.get("/api/alerts", params={"acknowledged": False}).json()["items"]
        assert all(a["id"] != alert_id for a in unacked)
        assert client.post("/api/alerts/acknowledge-all").status_code == 200
        assert client.get("/api/alerts", params={"acknowledged": False}).json()["total"] == 0
        assert client.post("/api/alerts/ALT-NONE/acknowledge").status_code == 404


class TestIntersectionManagement:
    def test_add_intersection_extends_live_network(self, client) -> None:
        created = client.post("/api/intersections", json={"name": "Airport Link", "grid_x": 3, "grid_y": 0})
        assert created.status_code == 201
        body = created.json()
        assert body["simulation_reset"] is True
        new_id = body["intersection"]["id"]
        assert new_id in client.runtime.engine.controllers
        assert client.get("/api/sensors", params={"intersection_id": new_id}).json()
        advance(client, 30)
        assert client.get(f"/api/intersections/{new_id}/traffic").json()["phase"] is not None
        assert client.get(f"/api/intersections/{new_id}/health").json()["sensors_total"] == 21

    def test_occupied_grid_rejected(self, client) -> None:
        assert client.post("/api/intersections", json={"name": "Clash", "grid_x": 0, "grid_y": 0}).status_code == 409

    def test_invalid_input_rejected(self, client) -> None:
        assert client.post("/api/intersections", json={"name": "x", "grid_x": 9, "grid_y": 9}).status_code == 422

    def test_rename_without_reset(self, client) -> None:
        body = client.patch("/api/intersections/INT-006", json={"name": "Riverside Bridge"}).json()
        assert body["intersection"]["name"] == "Riverside Bridge" and body["simulation_reset"] is False

    def test_disable_approach_rebuilds_network(self, client) -> None:
        body = client.patch("/api/intersections/INT-006/approaches/SOUTH", json={"enabled": False}).json()
        assert body["simulation_reset"] is True
        assert "SOUTH" not in client.runtime.engine.controllers["INT-006"].fsm.directions
        client.patch("/api/intersections/INT-006/approaches/SOUTH", json={"enabled": True})


class TestAnalyticsAndPredictions:
    def test_analytics_from_real_rows(self, client) -> None:
        body = client.get("/api/analytics/traffic").json()
        assert body["rows"] > 1000 and body["sources"]["SEEDED"] > 0
        assert len(body["by_intersection"]) >= 6
        assert body["hourly_profile"] and body["daily"] and body["congestion_levels"]

    def test_filters_narrow_results(self, client) -> None:
        everything = client.get("/api/analytics/traffic").json()["rows"]
        one = client.get("/api/analytics/traffic", params={"intersection_id": "INT-001", "direction": "NORTH"}).json()
        assert one["rows"] < everything
        assert {r["id"] for r in one["by_intersection"]} == {"INT-001"}

    def test_bad_range_rejected(self, client) -> None:
        params = {"start": "2026-02-01T00:00:00", "end": "2026-01-01T00:00:00"}
        assert client.get("/api/analytics/traffic", params=params).status_code == 422

    def test_csv_export(self, client) -> None:
        response = client.get("/api/analytics/export", params={"intersection_id": "INT-001"})
        assert response.headers["content-type"].startswith("text/csv")
        lines = response.text.strip().splitlines()
        assert lines[0].startswith("bucket_start") and len(lines) > 100

    def test_training_measures_all_models(self, client) -> None:
        assert client.runtime.predictions.train() is not None
        body = client.get("/api/predictions/evaluation").json()
        assert body["trained"] is True
        names = {m["model_name"] for m in body["metrics"]}
        assert names == {"Persistence", "LinearRegression", "RandomForest"}
        assert {m["target"] for m in body["metrics"]} == {"volume", "wait", "density"}
        assert all(m["mae"] >= 0 and m["rmse"] >= m["mae"] for m in body["metrics"])

    def test_backtest_series(self, client) -> None:
        rows = client.get("/api/predictions/backtest", params={"intersection_id": "INT-001"}).json()
        assert rows and {"actual_volume", "predicted_volume"} <= set(rows[0])

    def test_forecasts_are_stored(self, client) -> None:
        from datetime import UTC, datetime

        runtime = client.runtime
        runtime.predictions.last_forecast_bin = None
        now = datetime.fromtimestamp(runtime.engine.sim_epoch_now, tz=UTC).replace(tzinfo=None)
        runtime.predictions.forecast(now)
        rows = client.get("/api/predictions").json()
        assert len({r["intersection_id"] for r in rows}) >= 6
        assert rows[0]["predicted_level"] in ("LOW", "MODERATE", "HIGH", "SEVERE")
        targets = [(r["intersection_id"], r["target_at"]) for r in rows]
        assert len(targets) == len(set(targets)), "one forecast per intersection and time"


class TestAnalyticsFigures:
    def test_weighted_means_handle_mixed_bins_and_missing_speed(self) -> None:
        import pandas as pd

        from app.services.analytics import _prepare, _summaries

        frame = pd.DataFrame({
            "bin_seconds": [60, 300], "volume": [10, 30], "avg_wait_s": [10.0, 40.0], "avg_speed": [None, 8.0],
            "density": [20.0, 50.0], "avg_queue": [2.0, 8.0], "congestion_level": ["LOW", "HIGH"],
        })
        summary = _summaries(_prepare(frame), lambda _: 0)[0][1]
        assert summary["volume"] == 40
        assert summary["wait"] == 35.0, "weighted by bin length"
        assert summary["speed"] == 8.0, "a missing speed carries no weight"
        assert summary["congested_pct"] == 83.3


class TestCommitNotification:
    def test_clients_are_told_only_after_rows_are_readable(self, client) -> None:
        from sqlalchemy import select

        from app.models import TrafficAlert

        runtime = client.runtime
        seen: list[bool] = []
        original = runtime.writer.on_commit

        def check(kinds: list[str]) -> None:
            if "alert" in kinds:
                with runtime.database.session() as session:
                    seen.append(session.scalar(select(TrafficAlert).where(TrafficAlert.id == alert_id)) is not None)

        runtime.writer.on_commit = check
        try:
            runtime.processor.raise_alert("UNUSUAL_TRAFFIC", "WARNING", "commit order test", None, "commit-test")
            alert_id = runtime.processor.alerts["commit-test"]["id"]
            runtime.writer.flush()
        finally:
            runtime.processor.resolve_alert("commit-test")
            runtime.writer.flush()
            runtime.writer.on_commit = original
        assert seen and seen[0] is True


class TestSystem:
    def test_health(self, client) -> None:
        body = client.get("/api/system/health").json()
        assert body["database"]["backend"] == "sqlite"
        assert body["transport"]["mode"] == "LOCAL_FALLBACK"

    def test_config_round_trip_reaches_algorithm(self, client) -> None:
        config = client.get("/api/system/config").json()
        config["algorithm"]["weights"]["queue"] = 0.5
        config["algorithm"]["control_strategy"] = "FIXED_TIME"
        assert client.put("/api/system/config", json=config).status_code == 200
        assert client.runtime.processor.central.config.weights.queue == 0.5
        assert client.runtime.processor.central.strategy == "FIXED_TIME"
        config["algorithm"]["control_strategy"] = "ADAPTIVE"
        client.put("/api/system/config", json=config)

    def test_invalid_config_rejected(self, client) -> None:
        config = client.get("/api/system/config").json()
        config["congestion"]["queue"] = [30, 20, 10]
        assert client.put("/api/system/config", json=config).status_code == 422

    def test_reset_preserves_history(self, client) -> None:
        before = client.get("/api/analytics/traffic").json()["rows"]
        client.post("/api/simulation/reset")
        assert client.runtime.engine.vehicle_count() == 0
        assert client.get("/api/analytics/traffic").json()["rows"] >= before

    def test_cleanup_needs_explicit_confirmation(self, client) -> None:
        assert client.post("/api/system/cleanup", json={"older_than_days": 3, "confirm": False}).status_code == 422
        response = client.post("/api/system/cleanup", json={"older_than_days": 3, "confirm": True})
        assert response.status_code == 200 and response.json()["removed"]["traffic_metrics"] > 0

    def test_websocket_streams_state(self, client) -> None:
        with client.websocket_connect("/ws/live") as socket:
            kinds = {socket.receive_json()["type"] for _ in range(4)}
            assert {"topology", "series", "sensors", "live"} <= kinds
            advance(client, 1)
            seen = {socket.receive_json()["type"] for _ in range(2)}
            assert "frame" in seen or "live" in seen
