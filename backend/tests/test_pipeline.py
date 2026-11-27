"""Closed-loop tests: engine and processor connected only through the message bus."""

import pytest
from pydantic import ValidationError

from app.mqtt import topics
from app.schemas.messages import SensorReadingMessage, SignalStateMessage
from app.simulation.defaults import default_sensor_specs, default_specs
from app.simulation.headless import build_pipeline
from app.simulation.network import build_network
from app.simulation.scenarios import SCENARIOS, random_scenario


class RecordingSink:
    def __init__(self) -> None:
        self.rows: dict[str, list] = {}
        self.updates: dict[str, list] = {}

    def add(self, kind, row) -> None:
        self.rows.setdefault(kind, []).append(row)

    def update(self, kind, key, values) -> None:
        self.updates.setdefault(kind, []).append((key, values))


def assert_never_conflicting(pipeline, seconds: float) -> None:
    for _ in range(round(seconds / pipeline.engine.dt)):
        pipeline.engine.step()
        for controller in pipeline.engine.controllers.values():
            moving = [d for d, aspect in controller.fsm.heads().items() if aspect != "RED"]
            assert len(moving) <= 1


class TestNetwork:
    def test_demo_city_has_six_intersections_and_24_approaches(self) -> None:
        network = build_network(default_specs())
        assert len(network.intersections) == 6
        assert len(network.incoming) == 24
        assert len(network.outgoing) == 24

    def test_interior_road_is_shared_between_neighbours(self) -> None:
        network = build_network(default_specs())
        road = network.outgoing[("INT-001", "EAST")]
        assert road is network.incoming[("INT-002", "WEST")]

    def test_shortest_path_crosses_the_grid(self) -> None:
        path = build_network(default_specs()).shortest_path("INT-001", "INT-006")
        assert path[0] == "INT-001" and path[-1] == "INT-006" and len(path) == 4

    def test_no_u_turns(self) -> None:
        network = build_network(default_specs())
        for link in network.links:
            for out, _, _ in link.turns:
                assert (link.ux + out.ux, link.uy + out.uy) != (0, 0)


class TestTrafficDynamics:
    def test_vehicles_spawn_move_and_complete_trips(self) -> None:
        pipeline = build_pipeline(seed=3)
        pipeline.engine.apply_scenario(SCENARIOS["MORNING_RUSH"])
        pipeline.run(300)
        performance = pipeline.engine.performance()
        assert performance["vehicles"] > 20
        assert performance["completed_trips"] > 10
        assert 0 < performance["flow_efficiency"] <= 100

    def test_vehicles_never_overlap_on_a_link(self) -> None:
        pipeline = build_pipeline(seed=4)
        pipeline.engine.apply_scenario(SCENARIOS["HEAVY_CONGESTION"])
        for _ in range(1200):
            pipeline.engine.step()
            for link in pipeline.engine.network.links:
                for front, back in zip(link.vehicles, link.vehicles[1:], strict=False):
                    assert front.pos - front.length >= back.pos - 0.01

    def test_no_vehicle_enters_the_junction_on_red(self) -> None:
        pipeline = build_pipeline(seed=5)
        pipeline.engine.apply_scenario(SCENARIOS["MORNING_RUSH"])
        crossings = {}
        for _ in range(1600):
            before = {link.id: link.crossed_total for link in pipeline.engine.network.links if link.to_intersection}
            pipeline.engine.step()
            aspects = pipeline.engine.heads
            for link in pipeline.engine.network.links:
                if link.to_intersection and link.crossed_total > before[link.id]:
                    aspect = aspects.get((link.to_intersection, link.approach))
                    crossings[aspect] = crossings.get(aspect, 0) + 1
                    assert aspect in ("GREEN", "YELLOW")
        assert crossings["GREEN"] > 50

    def test_same_seed_is_reproducible(self) -> None:
        runs = []
        for _ in range(2):
            pipeline = build_pipeline(seed=11)
            pipeline.engine.epoch = 1_700_000_000.0
            pipeline.run(120)
            runs.append(pipeline.engine.spawned_total)
        assert runs[0] == runs[1]

    def test_scenarios_change_observable_demand(self) -> None:
        def volume(key: str) -> int:
            pipeline = build_pipeline(seed=2)
            pipeline.engine.apply_scenario(SCENARIOS[key])
            pipeline.run(300)
            return pipeline.engine.spawned_total

        assert volume("HEAVY_CONGESTION") > volume("MORNING_RUSH") * 1.2

    def test_every_scenario_and_random_scenario_runs_safely(self) -> None:
        for scenario in SCENARIOS.values():
            pipeline = build_pipeline(seed=6)
            pipeline.engine.apply_scenario(scenario)
            assert_never_conflicting(pipeline, 120)
        pipeline = build_pipeline(seed=6)
        pipeline.engine.apply_scenario(random_scenario(pipeline.engine.rng, list(pipeline.processor.intersections)))
        assert_never_conflicting(pipeline, 120)

    def test_reset_clears_traffic_but_keeps_the_clock_moving(self) -> None:
        pipeline = build_pipeline(seed=1)
        pipeline.engine.apply_scenario(SCENARIOS["MIXED"])
        pipeline.run(200)
        clock = pipeline.engine.sim_epoch_now
        pipeline.engine.reset()
        assert pipeline.engine.vehicle_count() == 0
        assert not pipeline.engine.incidents and not pipeline.engine.emergencies
        assert pipeline.engine.sim_epoch_now == pytest.approx(clock)


class TestWarmUp:
    def test_fills_roads_silently_and_leaves_the_clock_alone(self) -> None:
        sink = RecordingSink()
        pipeline = build_pipeline(seed=31, sink=sink)
        clock = pipeline.engine.sim_epoch_now
        pipeline.engine.warm_up(120)
        assert pipeline.engine.vehicle_count() > 5
        assert pipeline.processor.stats["readings"] == 0
        assert not sink.rows
        assert pipeline.engine.sim_epoch_now == pytest.approx(clock)
        pipeline.run(10)
        assert pipeline.processor.stats["readings"] > 0
        assert pipeline.engine.controllers["INT-001"].status == "ONLINE"


class TestSensors:
    def test_readings_track_the_simulation(self) -> None:
        pipeline = build_pipeline(seed=8)
        pipeline.engine.apply_scenario(SCENARIOS["MORNING_RUSH"])
        pipeline.run(400)
        link = pipeline.engine.network.incoming[("INT-001", "NORTH")]
        approach = pipeline.processor.approaches[("INT-001", "NORTH")]
        assert approach.density > 0
        actual = 100 * len(link.vehicles) / (link.length / 7.5)
        assert abs(approach.density - actual) < 30

    def test_every_published_reading_passes_validation(self) -> None:
        pipeline = build_pipeline(seed=9)
        pipeline.run(120)
        assert pipeline.processor.stats["readings"] > 500
        assert pipeline.processor.stats["invalid_payloads"] == 0

    def test_failed_sensor_goes_offline_alerts_and_recovers_on_restart(self) -> None:
        pipeline = build_pipeline(seed=10)
        pipeline.run(30)
        sensor_id = pipeline.engine.fail_sensors("INT-002", "NORTH", 1, 600)[0]
        pipeline.run(40)
        assert pipeline.processor.sensors[sensor_id].status == "OFFLINE"
        assert f"sensor:{sensor_id}" in pipeline.processor.alerts
        pipeline.engine.restart_sensor(sensor_id)
        pipeline.run(10)
        assert pipeline.processor.sensors[sensor_id].status == "ONLINE"
        assert f"sensor:{sensor_id}" not in pipeline.processor.alerts

    def test_algorithm_keeps_serving_an_approach_with_all_sensors_down(self) -> None:
        pipeline = build_pipeline(seed=12)
        pipeline.engine.apply_scenario(SCENARIOS["MORNING_RUSH"])
        pipeline.run(60)
        pipeline.engine.fail_sensors("INT-002", "NORTH", 5, 2000)
        controller = pipeline.engine.controllers["INT-002"]
        served = 0
        previous = None
        for _ in range(1200):
            pipeline.engine.step()
            if controller.fsm.active == "NORTH" and previous != "NORTH":
                served += 1
            previous = controller.fsm.active
        assert served >= 2


class TestPayloadValidation:
    GOOD = {
        "sensor_id": "SNS-001", "sensor_type": "TRAFFIC_DENSITY", "intersection_id": "INT-001",
        "direction": "NORTH", "status": "ACTIVE", "timestamp": "2026-01-01T00:00:00+00:00", "seq": 1,
        "traffic_density": 50,
    }

    def test_good_payload(self) -> None:
        assert SensorReadingMessage.model_validate(self.GOOD).traffic_density == 50

    @pytest.mark.parametrize(
        "patch",
        [
            {"sensor_id": "BAD"},
            {"direction": "UP"},
            {"traffic_density": 150},
            {"traffic_density": None},
            {"direction": None},
            {"timestamp": "yesterday"},
            {"unexpected": 1},
            {"simulated": False},
        ],
    )
    def test_bad_payload_rejected(self, patch) -> None:
        with pytest.raises(ValidationError):
            SensorReadingMessage.model_validate({**self.GOOD, **patch})

    def test_conflicting_state_report_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SignalStateMessage.model_validate({
                "intersection_id": "INT-001", "phase": "GREEN", "active_direction": "NORTH",
                "heads": {"NORTH": "GREEN", "EAST": "GREEN"}, "elapsed": 1, "planned_green": 10, "remaining": 9,
                "mode": "AUTOMATIC", "controller": "ONLINE", "min_green": 8, "max_green": 60,
                "timestamp": "2026-01-01T00:00:00+00:00",
            })

    def test_processor_counts_and_survives_bad_messages(self) -> None:
        pipeline = build_pipeline(seed=1)
        pipeline.processor.handle("traffic/sensors/SNS-001/readings", {"nonsense": True})
        assert pipeline.processor.stats["invalid_payloads"] == 1

    def test_unregistered_sensor_is_ignored(self) -> None:
        pipeline = build_pipeline(seed=1)
        pipeline.processor.handle(
            topics.sensor_readings("SNS-999"), {**self.GOOD, "sensor_id": "SNS-999"}
        )
        assert pipeline.processor.stats["unknown_sensor"] == 1


class TestAdaptiveControl:
    def test_decisions_are_recorded_with_reasoning_and_scores(self) -> None:
        sink = RecordingSink()
        pipeline = build_pipeline(seed=13, sink=sink)
        pipeline.engine.apply_scenario(SCENARIOS["MORNING_RUSH"])
        pipeline.run(300)
        decisions = sink.rows["decision"]
        assert decisions
        assert all(d["reason"] and len(d["scores"]) == 4 for d in decisions)

    def test_busier_approach_gets_more_green_than_quiet_one(self) -> None:
        sink = RecordingSink()
        pipeline = build_pipeline(seed=14, sink=sink)
        pipeline.engine.apply_scenario(SCENARIOS["MORNING_RUSH"])  # NORTH x1.5, SOUTH x0.7
        pipeline.run(1500)
        green = {}
        for row in sink.rows["signal_history"]:
            if row["signal_id"] == "SIG-002" and row["phase"] == "GREEN":
                green[row["direction"]] = green.get(row["direction"], 0) + row["duration_s"]
        assert green["NORTH"] > green["SOUTH"]

    def test_green_limits_hold_in_recorded_history(self) -> None:
        sink = RecordingSink()
        pipeline = build_pipeline(seed=15, sink=sink)
        pipeline.engine.apply_scenario(SCENARIOS["HEAVY_CONGESTION"])
        pipeline.run(900)
        rows = sink.rows["signal_history"]
        greens = [r["duration_s"] for r in rows if r["phase"] == "GREEN"]
        assert greens and min(greens) >= 8 - 0.51 and max(greens) <= 60 + 0.51
        assert all(r["duration_s"] >= 2.99 for r in rows if r["phase"] == "YELLOW")
        assert all(r["duration_s"] >= 1.99 for r in rows if r["phase"] == "ALL_RED")

    def test_no_approach_starves_under_overload(self) -> None:
        pipeline = build_pipeline(seed=16)
        pipeline.engine.apply_scenario(SCENARIOS["HEAVY_CONGESTION"])
        pipeline.run(300)
        last_green = {}
        worst = 0.0
        for _ in range(3600):
            pipeline.engine.step()
            for key, controller in pipeline.engine.controllers.items():
                for direction in controller.fsm.directions:
                    if controller.fsm.heads()[direction] == "GREEN":
                        last_green[(key, direction)] = pipeline.engine.time
                    elif (key, direction) in last_green:
                        worst = max(worst, pipeline.engine.time - last_green[(key, direction)])
        # Three maximum greens plus clearances, after the 120 s limit is reached.
        assert worst <= 120 + 3 * 65 + 10

    def test_adaptive_beats_fixed_time_under_rush_demand(self) -> None:
        from app.simulation.headless import run_case

        adaptive = run_case("MORNING_RUSH", 15, 21, "ADAPTIVE")
        fixed = run_case("MORNING_RUSH", 15, 21, "FIXED_TIME")
        assert adaptive["mean_wait_s"] < fixed["mean_wait_s"]

    def test_manual_mode_stops_adaptive_commands(self) -> None:
        pipeline = build_pipeline(seed=17)
        pipeline.run(30)
        pipeline.backend.publish(topics.signal_command("INT-001"), {
            "action": "SET_MODE", "intersection_id": "INT-001", "mode": "MANUAL", "source": "OPERATOR",
        })
        pipeline.run(5)
        before = pipeline.processor.intersections["INT-001"].last_decision
        pipeline.run(30)
        assert pipeline.processor.intersections["INT-001"].last_decision is before

    def test_command_for_disabled_direction_cannot_break_controller(self) -> None:
        pipeline = build_pipeline(seed=18)
        pipeline.run(20)
        pipeline.engine.on_command("t", {"action": "SWITCH", "intersection_id": "INT-001"})
        pipeline.engine.on_command("t", {"action": "EXPLODE", "intersection_id": "INT-001"})
        assert_never_conflicting(pipeline, 30)
        assert pipeline.engine.controllers["INT-001"].rejected_commands == 1


class TestEmergency:
    def test_vehicle_gets_priority_and_signals_recover(self) -> None:
        pipeline = build_pipeline(seed=19)
        pipeline.engine.apply_scenario(SCENARIOS["MORNING_RUSH"])
        pipeline.run(200)
        details = pipeline.engine.dispatch_emergency("AMBULANCE", "INT-001", "INT-006")
        vehicle_id = details["vehicle_id"]
        preempted = set()
        for _ in range(800):
            pipeline.engine.step()
            for key, controller in pipeline.engine.controllers.items():
                moving = [d for d, a in controller.fsm.heads().items() if a != "RED"]
                assert len(moving) <= 1
                if controller.fsm.preempt and controller.fsm.preempt.vehicle_id == vehicle_id:
                    preempted.add(key)
            if vehicle_id not in pipeline.engine.emergencies:
                break
        assert vehicle_id not in pipeline.engine.emergencies, "vehicle should arrive"
        assert set(details["route"]) <= preempted
        pipeline.run(10)
        assert all(c.fsm.preempt is None for c in pipeline.engine.controllers.values())
        assert pipeline.processor.preemption.active == {}
        assert not [k for k in pipeline.processor.alerts if k.startswith("emergency:")]

    def test_preemption_never_shortcuts_clearance(self) -> None:
        sink = RecordingSink()
        pipeline = build_pipeline(seed=20, sink=sink)
        pipeline.engine.apply_scenario(SCENARIOS["EMERGENCY"])
        pipeline.run(400)
        rows = sink.rows["signal_history"]
        assert any("preemption" in (r["end_reason"] or "") for r in rows)
        assert all(r["duration_s"] >= 2.99 for r in rows if r["phase"] == "YELLOW")
        assert all(r["duration_s"] >= 1.99 for r in rows if r["phase"] == "ALL_RED")
        assert all(r["duration_s"] >= 7.49 for r in rows if r["phase"] == "GREEN")

    def test_priority_saves_time(self) -> None:
        def stopped_time(preempt: bool) -> float:
            sink = RecordingSink()
            pipeline = build_pipeline(seed=22, sink=sink)
            if not preempt:
                pipeline.processor.preemption.config = type(pipeline.processor.preemption.config)(0, 0)
            pipeline.engine.apply_scenario(SCENARIOS["MORNING_RUSH"])
            pipeline.run(200)
            pipeline.engine.dispatch_emergency("AMBULANCE", "INT-001", "INT-006")
            pipeline.run(400)
            updates = [v for _, v in sink.updates["emergency"] if v.get("status") == "ARRIVED"]
            return updates[0]["wait_time_s"]

        assert stopped_time(True) < stopped_time(False)

    def test_bad_route_rejected(self) -> None:
        pipeline = build_pipeline(seed=1)
        with pytest.raises(ValueError):
            pipeline.engine.dispatch_emergency("AMBULANCE", "INT-001", "INT-001")
        with pytest.raises(ValueError):
            pipeline.engine.dispatch_emergency("TANK", "INT-001", "INT-002")

    def test_cancel_releases_priority(self) -> None:
        pipeline = build_pipeline(seed=23)
        pipeline.run(30)
        vehicle_id = pipeline.engine.dispatch_emergency("POLICE", "INT-001", "INT-003")["vehicle_id"]
        pipeline.run(6)
        pipeline.engine.cancel_emergency(vehicle_id)
        pipeline.run(5)
        assert all(c.fsm.preempt is None for c in pipeline.engine.controllers.values())


class TestIncidents:
    @pytest.mark.parametrize(
        "incident_type", ["MAJOR_ACCIDENT", "ROAD_BLOCKAGE", "MINOR_ACCIDENT", "SUDDEN_SLOWDOWN", "LANE_CLOSURE"]
    )
    def test_injected_incident_is_detected_from_sensors(self, incident_type) -> None:
        sink = RecordingSink()
        pipeline = build_pipeline(seed=24, sink=sink)
        pipeline.engine.params.volume_factor = 1.3
        pipeline.engine.apply_scenario(SCENARIOS["ACCIDENT"].__class__("T", "T", "T", demand_multiplier=1.0))
        pipeline.run(300)
        incident = pipeline.engine.inject_incident(incident_type, "INT-002", "EAST", 300)
        pipeline.run(280)
        updates = [v for key, v in sink.updates.get("incident", []) if key == incident["incident_id"]]
        assert any(v.get("status") == "DETECTED" for v in updates), incident_type

    def test_normal_traffic_raises_no_false_incidents(self) -> None:
        sink = RecordingSink()
        pipeline = build_pipeline(seed=25, sink=sink)
        pipeline.engine.apply_scenario(SCENARIOS["ACCIDENT"].__class__("T", "T", "T", demand_multiplier=1.0))
        pipeline.run(1800)
        assert not sink.rows.get("incident")

    def test_incident_lifecycle_alerts(self) -> None:
        pipeline = build_pipeline(seed=26)
        pipeline.run(30)
        details = pipeline.engine.inject_incident("MAJOR_ACCIDENT", "INT-001", "EAST", 40)
        pipeline.run(5)
        assert f"injected:{details['incident_id']}" in pipeline.processor.alerts
        pipeline.run(40)
        assert f"injected:{details['incident_id']}" not in pipeline.processor.alerts
        assert not pipeline.engine.incidents

    def test_blockage_stops_traffic_at_the_obstruction(self) -> None:
        pipeline = build_pipeline(seed=27)
        pipeline.engine.params.volume_factor = 1.5
        pipeline.engine.apply_scenario(SCENARIOS["ACCIDENT"].__class__("T", "T", "T"))
        pipeline.run(100)
        pipeline.engine.inject_incident("ROAD_BLOCKAGE", "INT-001", "NORTH", 300)
        pipeline.run(150)
        link = pipeline.engine.network.incoming[("INT-001", "NORTH")]
        block = link.incidents[0].start
        assert all(v.pos <= block for v in link.vehicles)
        assert sum(v.speed < 1 for v in link.vehicles) >= 3

    def test_unknown_incident_rejected(self) -> None:
        pipeline = build_pipeline(seed=1)
        with pytest.raises(ValueError):
            pipeline.engine.inject_incident("ALIENS", "INT-001", "NORTH")


class TestCongestionLoop:
    def test_overload_opens_alert_and_record(self) -> None:
        sink = RecordingSink()
        pipeline = build_pipeline(seed=28, sink=sink)
        pipeline.engine.apply_scenario(SCENARIOS["HEAVY_CONGESTION"])
        pipeline.run(600)
        assert sink.rows.get("congestion")
        assert any(k.startswith("congestion:") for k in pipeline.processor.alerts)

    def test_light_traffic_stays_quiet(self) -> None:
        sink = RecordingSink()
        pipeline = build_pipeline(seed=29, sink=sink)
        pipeline.engine.params.volume_factor = 0.5
        pipeline.engine.apply_scenario(SCENARIOS["ACCIDENT"].__class__("T", "T", "T"))
        pipeline.run(900)
        assert not sink.rows.get("congestion")

    def test_metrics_roll_up_each_minute(self) -> None:
        sink = RecordingSink()
        pipeline = build_pipeline(seed=30, sink=sink)
        pipeline.engine.apply_scenario(SCENARIOS["MORNING_RUSH"])
        pipeline.run(240)
        rows = sink.rows["metric"]
        assert rows and {r["bin_seconds"] for r in rows} == {60}
        assert sum(r["volume"] for r in rows) > 0


def test_sensor_fleet_size() -> None:
    assert len(default_sensor_specs()) == 6 * (4 * 5 + 1)
