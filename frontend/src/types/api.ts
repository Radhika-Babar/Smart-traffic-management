/** Contracts for the REST and WebSocket payloads. Mirrors backend/app/schemas. */

export type Direction = "NORTH" | "EAST" | "SOUTH" | "WEST";
export type Level = "LOW" | "MODERATE" | "HIGH" | "SEVERE";
export type Phase = "RED" | "GREEN" | "YELLOW" | "ALL_RED";
export type Aspect = "RED" | "YELLOW" | "GREEN";
export type SignalMode = "AUTOMATIC" | "MANUAL";

export interface ApproachLive {
  direction: Direction;
  queue: number;
  approaching: number;
  avg_wait_s: number;
  max_wait_s: number;
  density: number;
  occupancy: number;
  speed: number | null;
  level: Level;
}

export interface ApproachScore {
  direction: Direction;
  queue: number;
  waiting: number;
  density: number;
  approaching: number;
  speed: number | null;
  queue_norm: number;
  waiting_norm: number;
  density_norm: number;
  demand_norm: number;
  speed_norm: number;
  priority: number;
  has_demand: boolean;
  starving: boolean;
  degraded: boolean;
  red_seconds: number;
}

export interface Decision {
  action: string;
  direction: Direction | null;
  green_s: number;
  reason: string;
  scores: ApproachScore[];
  decided_at: string;
}

export interface IntersectionLive {
  id: string;
  name: string;
  phase: Phase | null;
  active_direction: Direction | null;
  heads: Partial<Record<Direction, Aspect>>;
  elapsed: number;
  planned_green: number;
  remaining: number;
  mode: SignalMode;
  controller: "ONLINE" | "FAILSAFE" | "FAILED";
  preempt_vehicle: string | null;
  preempt_direction: Direction | null;
  approaches: ApproachLive[];
  congestion: Level;
  vehicles: number;
  active_sensors: number;
  total_sensors: number;
  last_decision: Decision | null;
  scores: ApproachScore[];
}

export interface NetworkHealth {
  score: number;
  status: "HEALTHY" | "DEGRADED" | "CRITICAL";
  sensors: number;
  controllers: number;
  transport: Transport;
}

export type Transport = "MQTT" | "LOCAL_FALLBACK";

export interface Overview {
  total_intersections: number;
  active_sensors: number;
  total_sensors: number;
  offline_sensors: number;
  total_vehicles: number;
  average_density: number;
  average_speed: number | null;
  total_queue: number;
  flow_vph: number;
  active_congestion_alerts: number;
  active_alerts: number;
  active_emergencies: number;
  active_incidents: number;
  average_wait_s: number;
  average_trip_wait_s: number | null;
  flow_efficiency: number | null;
  completed_trips: number;
  auto_signals: number;
  network_health: NetworkHealth;
}

export interface SimulationStatus {
  state: "STOPPED" | "RUNNING" | "PAUSED";
  speed: number;
  clock: string;
  sim_seconds: number;
  run_id: number | null;
  scenario: string;
  scenario_name: string;
  weather: "CLEAR" | "RAIN";
  volume_factor: number;
  spawn_rate_vph: number | null;
  intersection_factors: Record<string, number>;
  control_strategy: "ADAPTIVE" | "FIXED_TIME";
  tick_ms: number;
  worker_alive: boolean;
}

export interface TransportStatus {
  mode: Transport;
  enabled: boolean;
  broker: string;
  field_connected: boolean;
  backend_connected: boolean;
  reconnects: number;
  published: Record<string, number>;
  received: Record<string, number>;
  malformed: number;
  invalid_payloads: number;
  backlog: number;
}

export interface Alert {
  id: string;
  category: string;
  severity: "INFO" | "WARNING" | "CRITICAL";
  description: string;
  intersection_id: string | null;
  raised_at: string;
  acknowledged: boolean;
  acknowledged_at?: string | null;
  resolved: boolean;
  resolved_at?: string | null;
  source: string;
}

export interface EmergencyLive {
  vehicle_id: string;
  vehicle_type: "AMBULANCE" | "FIRE_ENGINE" | "POLICE";
  status: string;
  x: number;
  y: number;
  speed: number;
  next_intersection: string | null;
  approach: Direction | null;
  distance_m: number | null;
  eta_s: number | null;
  route: string[];
  route_index: number;
  preemptions: number;
}

export interface SeriesPoint {
  t: string;
  density: number;
  queue: number;
  wait: number;
  speed: number | null;
  volume: number;
}

export interface LivePayload {
  overview: Overview;
  intersections: IntersectionLive[];
  alerts: Alert[];
  emergencies: EmergencyLive[];
  simulation: SimulationStatus;
  transport: TransportStatus;
  point: SeriesPoint | null;
}

export interface SystemEvent {
  occurred_at: string;
  category: string;
  severity: string;
  message: string;
  intersection_id: string | null;
}

export interface Topology {
  intersections: { id: string; name: string; x: number; y: number; directions: Direction[] }[];
  links: {
    id: string;
    x1: number;
    y1: number;
    x2: number;
    y2: number;
    to_intersection: string | null;
    approach: Direction | null;
  }[];
}

/** [id, x, y, heading, category, speed] */
export type VehicleRow = [string, number, number, number, string, number];

export interface Frame {
  time: number;
  clock: string;
  vehicles: VehicleRow[];
  counts: Record<string, number>;
  signals: Record<
    string,
    {
      phase: Phase;
      heads: Partial<Record<Direction, Aspect>>;
      active: Direction | null;
      elapsed: number;
      remaining: number;
      mode: SignalMode;
      preempt: string | null;
      status: string;
    }
  >;
  incidents: { id: string; type: string; intersection_id: string; direction: Direction; x: number; y: number }[];
}

export interface SensorLive {
  id: string;
  sensor_type: string;
  intersection_id: string;
  direction: Direction | null;
  reporting_interval_s: number;
  enabled: boolean;
  status: "ONLINE" | "OFFLINE" | "DISABLED";
  readings_received: number;
  latest: Record<string, number | string>;
}

export interface Reading {
  recorded_at: string;
  vehicle_count: number | null;
  traffic_density: number | null;
  average_speed: number | null;
  occupancy_percentage: number | null;
  queue_length: number | null;
  approaching: number | null;
  avg_wait_s: number | null;
  max_wait_s: number | null;
  temperature_c: number | null;
  rain_mm_h: number | null;
  visibility_m: number | null;
}

export interface SignalConfig {
  id: string;
  intersection_id: string;
  mode: SignalMode;
  min_green_s: number;
  max_green_s: number;
  default_green_s: number;
  yellow_s: number;
  all_red_s: number;
  preempt_max_hold_s: number;
}

export interface IntersectionConfig {
  id: string;
  name: string;
  description: string | null;
  grid_x: number;
  grid_y: number;
  speed_limit_ms: number;
  approaches: { direction: Direction; enabled: boolean; lanes: number; base_demand_vph: number }[];
  signal: SignalConfig;
  sensor_count: number;
}

export interface DecisionRecord {
  id: number;
  decided_at: string;
  action: string;
  direction: Direction | null;
  green_s: number;
  reason: string;
  scores: ApproachScore[];
}

export interface SignalHistory {
  id: number;
  phase: Phase;
  direction: Direction | null;
  started_at: string;
  ended_at: string;
  duration_s: number;
  end_reason: string | null;
  mode: SignalMode;
}

export interface EmergencyRecord {
  id: string;
  vehicle_type: string;
  origin_id: string | null;
  destination_id: string | null;
  route: string[];
  status: string;
  dispatched_at: string;
  finished_at: string | null;
  travel_time_s: number | null;
  wait_time_s: number | null;
  stops: number | null;
  preemptions: number;
}

export interface Incident {
  id: string;
  incident_type: string;
  origin: "INJECTED" | "DETECTED";
  intersection_id: string | null;
  direction: Direction;
  severity: string;
  affected_lanes: string | null;
  status: "INJECTED" | "DETECTED" | "RESOLVED";
  injected_at: string | null;
  detected_at: string | null;
  resolved_at: string | null;
  detection_score: number | null;
  evidence: string[] | null;
  recommended_response: string | null;
}

export interface CongestionRecord {
  id: string;
  intersection_id: string | null;
  direction: Direction;
  severity: Level;
  detected_at: string;
  resolved_at: string | null;
  status: string;
  recommended_action: string;
}

export interface Scenario {
  key: string;
  name: string;
  description: string;
  demand_multiplier: number;
  follows_clock: boolean;
  weather: string;
  events: number;
}

export interface Prediction {
  intersection_id: string;
  generated_at: string;
  target_at: string;
  model_name: string;
  predicted_volume: number;
  predicted_wait_s: number;
  predicted_density: number;
  predicted_level: Level;
  actual_volume: number | null;
  actual_wait_s: number | null;
  actual_density: number | null;
}

export interface ModelMetric {
  model_name: string;
  target: string;
  mae: number;
  rmse: number;
  r2: number;
  train_rows: number;
  test_rows: number;
  feature_importance: Record<string, number> | null;
  selected: boolean;
}

export interface Evaluation {
  trained: boolean;
  training: boolean;
  last_error: string | null;
  run_id: string | null;
  trained_at: string | null;
  split_at: string | null;
  train_rows: number;
  test_rows: number;
  selected: Record<string, string>;
  metrics: ModelMetric[];
}

export interface BacktestPoint {
  t: string;
  actual_volume: number;
  predicted_volume: number;
  actual_wait: number;
  predicted_wait: number;
  actual_density: number;
  predicted_density: number;
}

export interface Summary {
  volume: number;
  wait: number | null;
  speed: number | null;
  density: number | null;
  queue: number | null;
  congested_pct: number;
}

export interface Analytics {
  range: { start: string; end: string };
  rows: number;
  sources: Record<string, number>;
  summary: Summary | null;
  series: (Summary & { t: string })[];
  hourly_profile: (Summary & { hour: number })[];
  daily: (Summary & { date: string })[];
  by_intersection: (Summary & { id: string; name: string })[];
  by_direction: (Summary & { direction: Direction })[];
  congestion_levels: { level: Level; share: number }[];
  congestion_events: { id: string; name: string; HIGH: number; SEVERE: number }[];
  signal_utilization: {
    intersection_id: string;
    direction: Direction;
    green_s: number;
    phases: number;
    share: number;
    avg_green_s: number;
  }[];
  sensor_uptime: { id: string; name: string; uptime: number; sensors: number; window_s: number }[];
  emergency: {
    completed: number;
    avg_travel_s: number | null;
    avg_wait_s: number | null;
    avg_preemptions: number | null;
    by_type: { type: string; count: number; avg_travel_s: number }[];
  };
}

export interface SystemHealth {
  status: string;
  uptime_s: number;
  database: { backend: string; rows_written: number; pending: number; dropped: number };
  transport: TransportStatus;
  websocket_clients: number;
}

export interface SystemSettings {
  algorithm: {
    weights: { queue: number; waiting: number; density: number; demand: number; speed: number };
    max_wait_limit_s: number;
    hysteresis: number;
    control_strategy: "ADAPTIVE" | "FIXED_TIME";
  };
  congestion: {
    queue: [number, number, number];
    occupancy: [number, number, number];
    wait: [number, number, number];
    speed: [number, number, number];
    alert_level: string;
    raise_after_s: number;
    clear_after_s: number;
  };
  sensor_mtbf_hours: number;
}
