import { useMemo, useState } from "react";

import { Legend, TimeChart } from "@/charts";
import {
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  LoadingRows,
  PageHeader,
  Panel,
  Pill,
} from "@/components/common";
import { Button, Dialog, Input, Select, Switch } from "@/components/ui/primitives";
import { useAction, useFetch } from "@/hooks/useFetch";
import { api } from "@/services/api";
import { useLive } from "@/stores/live";
import type { Reading, SensorLive } from "@/types/api";
import { clock, DIRECTIONS, fixed, title } from "@/utils/format";

const TYPES = [
  "VEHICLE_DETECTION",
  "TRAFFIC_DENSITY",
  "VEHICLE_COUNT",
  "ROAD_OCCUPANCY",
  "AVERAGE_SPEED",
  "ENVIRONMENTAL",
];

const METRIC: Record<string, { key: keyof Reading; name: string; unit: string }> = {
  VEHICLE_DETECTION: { key: "queue_length", name: "Queue", unit: "vehicles" },
  TRAFFIC_DENSITY: { key: "traffic_density", name: "Density", unit: "%" },
  VEHICLE_COUNT: { key: "vehicle_count", name: "Count", unit: "vehicles" },
  ROAD_OCCUPANCY: { key: "occupancy_percentage", name: "Occupancy", unit: "%" },
  AVERAGE_SPEED: { key: "average_speed", name: "Speed", unit: "m/s" },
  ENVIRONMENTAL: { key: "temperature_c", name: "Temperature", unit: "°C" },
};

function reading(sensor: SensorLive): string {
  const entries = Object.entries(sensor.latest).filter(([key]) => key !== "timestamp");
  return entries.length ? entries.map(([key, value]) => `${title(key)} ${value}`).join(", ") : "–";
}

function Detail({ sensor, onClose }: { sensor: SensorLive; onClose: () => void }) {
  const readings = useFetch(
    () => api.get<Reading[]>(`/api/sensors/${sensor.id}/readings?limit=120`),
    sensor.id,
    sensor.readings_received,
  );
  const metric = METRIC[sensor.sensor_type];
  const { run, busy } = useAction();
  const [interval, setIntervalValue] = useState(String(sensor.reporting_interval_s));
  return (
    <Dialog open onClose={onClose} title={`${sensor.id} · ${title(sensor.sensor_type)}`}>
      <p className="mb-3 text-[13px] text-ink-2">
        {sensor.intersection_id}
        {sensor.direction ? ` · ${title(sensor.direction)} approach` : " · site sensor"} · virtual device,{" "}
        {fixed(sensor.readings_received)} readings this run
      </p>
      {readings.error ? (
        <ErrorState message={readings.error} onRetry={readings.reload} />
      ) : (
        <>
          <Legend items={[{ name: `${metric.name} (${metric.unit})`, color: "#3987e5" }]} />
          <TimeChart
            data={readings.data ?? []}
            x="recorded_at"
            series={[{ key: metric.key, name: metric.name, color: "#3987e5" }]}
            format={clock}
            unit={metric.unit}
            height={170}
          />
        </>
      )}
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <Field label="Reporting interval (s)">
          <Input
            type="number"
            min={1}
            max={300}
            className="w-28"
            value={interval}
            onChange={(event) => setIntervalValue(event.target.value)}
          />
        </Field>
        <Button
          disabled={busy}
          onClick={() =>
            run(
              () => api.patch(`/api/sensors/${sensor.id}`, { reporting_interval_s: Number(interval) }),
              "Reporting interval updated",
            )
          }
        >
          Save interval
        </Button>
        <label className="ml-auto flex items-center gap-2 text-[13px]">
          Enabled
          <Switch
            checked={sensor.enabled}
            label={`Enable ${sensor.id}`}
            onChange={(enabled) => run(() => api.patch(`/api/sensors/${sensor.id}`, { enabled }))}
          />
        </label>
      </div>
    </Dialog>
  );
}

function Register({ onClose, intersections }: { onClose: () => void; intersections: string[] }) {
  const [sensorType, setType] = useState("AVERAGE_SPEED");
  const [intersectionId, setIntersection] = useState(intersections[0] ?? "");
  const [direction, setDirection] = useState("NORTH");
  const [interval, setIntervalValue] = useState("5");
  const { run, busy } = useAction();
  return (
    <Dialog open onClose={onClose} title="Register virtual sensor">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Sensor type">
          <Select
            label="Sensor type"
            className="w-full"
            value={sensorType}
            onChange={setType}
            options={TYPES.map((t) => ({ value: t, label: title(t) }))}
          />
        </Field>
        <Field label="Intersection">
          <Select
            label="Intersection"
            className="w-full"
            value={intersectionId}
            onChange={setIntersection}
            options={intersections.map((i) => ({ value: i, label: i }))}
          />
        </Field>
        {sensorType !== "ENVIRONMENTAL" ? (
          <Field label="Road direction">
            <Select
              label="Road direction"
              className="w-full"
              value={direction}
              onChange={setDirection}
              options={DIRECTIONS.map((d) => ({ value: d, label: title(d) }))}
            />
          </Field>
        ) : null}
        <Field label="Reporting interval (s)">
          <Input type="number" min={1} max={300} value={interval} onChange={(e) => setIntervalValue(e.target.value)} />
        </Field>
      </div>
      <div className="mt-4 flex justify-end">
        <Button
          variant="primary"
          disabled={busy}
          onClick={async () => {
            const created = await run(
              () =>
                api.post("/api/sensors", {
                  sensor_type: sensorType,
                  intersection_id: intersectionId,
                  direction: sensorType === "ENVIRONMENTAL" ? null : direction,
                  reporting_interval_s: Number(interval),
                }),
              "Sensor registered and reporting",
            );
            if (created) onClose();
          }}
        >
          Register sensor
        </Button>
      </div>
    </Dialog>
  );
}

export function Sensors() {
  const sensors = useLive((s) => s.sensors);
  const intersections = useLive((s) => s.live?.intersections) ?? [];
  const [site, setSite] = useState("ALL");
  const [type, setType] = useState("ALL");
  const [status, setStatus] = useState("ALL");
  const [openId, setOpenId] = useState<string | null>(null);
  const [registering, setRegistering] = useState(false);
  const { run } = useAction();

  const rows = useMemo(
    () =>
      sensors.filter(
        (s) =>
          (site === "ALL" || s.intersection_id === site) &&
          (type === "ALL" || s.sensor_type === type) &&
          (status === "ALL" || s.status === status),
      ),
    [sensors, site, type, status],
  );
  const offline = sensors.filter((s) => s.status === "OFFLINE");
  const open = sensors.find((s) => s.id === openId);

  return (
    <>
      <PageHeader
        name="IoT Sensors"
        finding={
          !sensors.length
            ? "Waiting for the sensor fleet"
            : offline.length
              ? `${offline.length} of ${sensors.length} virtual sensors have stopped reporting`
              : `All ${sensors.filter((s) => s.enabled).length} enabled virtual sensors are reporting`
        }
        actions={
          <Button variant="primary" onClick={() => setRegistering(true)}>
            Register sensor
          </Button>
        }
      />
      <Panel flush>
        <div className="flex flex-wrap gap-2 p-3">
          <Select
            label="Filter by intersection"
            className="w-40"
            value={site}
            onChange={setSite}
            options={[{ value: "ALL", label: "All intersections" }, ...intersections.map((i) => ({ value: i.id, label: i.name }))]}
          />
          <Select
            label="Filter by type"
            className="w-44"
            value={type}
            onChange={setType}
            options={[{ value: "ALL", label: "All types" }, ...TYPES.map((t) => ({ value: t, label: title(t) }))]}
          />
          <Select
            label="Filter by status"
            className="w-32"
            value={status}
            onChange={setStatus}
            options={[
              { value: "ALL", label: "Any status" },
              { value: "ONLINE", label: "Online" },
              { value: "OFFLINE", label: "Offline" },
              { value: "DISABLED", label: "Disabled" },
            ]}
          />
          <span className="tnum ml-auto self-center text-xs text-ink-3">
            {rows.length} of {sensors.length} sensors
          </span>
        </div>
        <div className="max-h-[calc(100vh-210px)] overflow-y-auto px-4 pb-2">
          {!sensors.length ? (
            <LoadingRows rows={8} />
          ) : rows.length ? (
            <DataTable>
              <thead className="sticky top-0 bg-card">
                <tr>
                  <th>Sensor</th>
                  <th>Type</th>
                  <th>Location</th>
                  <th>Latest reading</th>
                  <th className="!text-right">Interval</th>
                  <th className="!text-right">Readings</th>
                  <th className="!text-right">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((sensor) => (
                  <tr key={sensor.id} className="hover:bg-card-hover">
                    <td>
                      <button className="tnum font-medium hover:text-accent" onClick={() => setOpenId(sensor.id)}>
                        {sensor.id}
                      </button>
                    </td>
                    <td className="whitespace-nowrap text-ink-2">{title(sensor.sensor_type)}</td>
                    <td className="whitespace-nowrap text-ink-2">
                      {sensor.intersection_id} {sensor.direction ? title(sensor.direction) : "site"}
                    </td>
                    <td className="tnum max-w-72 truncate text-ink-2">{reading(sensor)}</td>
                    <td className="tnum text-right text-ink-2">{sensor.reporting_interval_s} s</td>
                    <td className="tnum text-right text-ink-2">{fixed(sensor.readings_received)}</td>
                    <td className="text-right">
                      {sensor.status === "OFFLINE" ? (
                        <Button size="sm" onClick={() => run(() => api.post(`/api/sensors/${sensor.id}/restart`), `${sensor.id} restarting`)}>
                          Restart
                        </Button>
                      ) : (
                        <Pill tone={sensor.status === "ONLINE" ? "good" : "neutral"}>{title(sensor.status)}</Pill>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          ) : (
            <EmptyState message="No sensors match these filters" />
          )}
        </div>
      </Panel>
      {open ? <Detail sensor={open} onClose={() => setOpenId(null)} /> : null}
      {registering ? (
        <Register onClose={() => setRegistering(false)} intersections={intersections.map((i) => i.id)} />
      ) : null}
    </>
  );
}
