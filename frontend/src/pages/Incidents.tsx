import { useState } from "react";

import { DataTable, EmptyState, ErrorState, Field, LevelBadge, LoadingRows, PageHeader, Panel, Pill } from "@/components/common";
import { Button, Select, TabList, TabPanel, Tabs } from "@/components/ui/primitives";
import { useAction, useFetch } from "@/hooks/useFetch";
import { api } from "@/services/api";
import { useLive } from "@/stores/live";
import type { CongestionRecord, Incident } from "@/types/api";
import { dateTime, DIRECTIONS, duration, fixed, secondsBetween, title } from "@/utils/format";

const TYPES = ["MINOR_ACCIDENT", "MAJOR_ACCIDENT", "ROAD_BLOCKAGE", "SUDDEN_SLOWDOWN", "LANE_CLOSURE"];

function Inject() {
  const intersections = useLive((s) => s.live?.intersections) ?? [];
  const [type, setType] = useState("MAJOR_ACCIDENT");
  const [site, setSite] = useState("INT-002");
  const [direction, setDirection] = useState("EAST");
  const { run, busy } = useAction();
  return (
    <div className="flex flex-wrap items-end gap-2">
      <Field label="Simulated event">
        <Select label="Incident type" className="w-44" value={type} onChange={setType} options={TYPES.map((t) => ({ value: t, label: title(t) }))} />
      </Field>
      <Field label="Intersection">
        <Select
          label="Incident intersection"
          className="w-44"
          value={site}
          onChange={setSite}
          options={intersections.map((i) => ({ value: i.id, label: i.name }))}
        />
      </Field>
      <Field label="Approach">
        <Select label="Incident approach" className="w-28" value={direction} onChange={setDirection} options={DIRECTIONS.map((d) => ({ value: d, label: title(d) }))} />
      </Field>
      <Button
        variant="primary"
        disabled={busy}
        onClick={() =>
          run(
            () => api.post("/api/incidents/simulate", { incident_type: type, intersection_id: site, direction }),
            "Injected. The detector sees only sensor data, so give it time to react",
          )
        }
      >
        Inject incident
      </Button>
    </div>
  );
}

function IncidentRow({ item }: { item: Incident }) {
  const { run, busy } = useAction();
  const latency = secondsBetween(item.injected_at, item.detected_at);
  const open = item.status !== "RESOLVED";
  return (
    <div className="border-t border-line py-2.5 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-medium">
          {item.origin === "DETECTED" ? "Unusual traffic" : title(item.incident_type)}
        </span>
        <span className="text-[13px] text-ink-2">
          {item.intersection_id} {title(item.direction)}
        </span>
        <Pill tone={item.severity === "MAJOR" ? "bad" : "warn"}>{title(item.severity)}</Pill>
        <Pill tone={item.status === "RESOLVED" ? "neutral" : item.status === "DETECTED" ? "bad" : "info"}>
          {item.status === "INJECTED" ? "Awaiting detection" : title(item.status)}
        </Pill>
        <Pill tone="info">Simulated</Pill>
        {open && item.origin === "INJECTED" ? (
          <Button size="sm" className="ml-auto" disabled={busy} onClick={() => run(() => api.post(`/api/incidents/${item.id}/resolve`))}>
            Clear
          </Button>
        ) : null}
      </div>
      <div className="tnum mt-1 flex flex-wrap gap-x-4 text-xs text-ink-3">
        <span>Injected {dateTime(item.injected_at)}</span>
        <span>Detected {dateTime(item.detected_at)}</span>
        {latency !== null ? <span>Detection took {duration(latency)}</span> : null}
        {item.detection_score !== null ? <span>Score {fixed(item.detection_score, 2)}</span> : null}
        {item.affected_lanes ? <span>{item.affected_lanes}</span> : null}
      </div>
      {item.evidence?.length ? <p className="mt-1 text-[13px] text-ink-2">{item.evidence.join(". ")}</p> : null}
      {item.recommended_response ? <p className="text-xs text-ink-3">Response: {item.recommended_response}</p> : null}
    </div>
  );
}

export function Incidents() {
  const version = useLive((s) => s.incidentsVersion);
  const incidents = useFetch(() => api.get<Incident[]>("/api/incidents?limit=30"), "incidents", version);
  const congestion = useFetch(() => api.get<CongestionRecord[]>("/api/congestion?limit=30"), "congestion", version);
  const open = incidents.data?.filter((i) => i.status !== "RESOLVED").length ?? 0;

  return (
    <>
      <PageHeader
        name="Incidents"
        finding={`${open} simulated incident${open === 1 ? "" : "s"} open. Detection reads sensor patterns only, not cameras or ground truth`}
      />
      <Panel name="Simulate an incident" className="mb-3">
        <Inject />
      </Panel>
      <Tabs defaultValue="incidents">
        <TabList
          tabs={[
            { value: "incidents", label: "Incident log" },
            { value: "congestion", label: "Congestion log" },
          ]}
        />
        <TabPanel value="incidents" className="pt-3">
          <Panel>
            {incidents.error ? (
              <ErrorState message={incidents.error} onRetry={incidents.reload} />
            ) : incidents.loading && !incidents.data ? (
              <LoadingRows />
            ) : incidents.data?.length ? (
              incidents.data.map((item) => <IncidentRow key={item.id} item={item} />)
            ) : (
              <EmptyState message="No incidents recorded" detail="Inject one above to watch detection work" />
            )}
          </Panel>
        </TabPanel>
        <TabPanel value="congestion" className="pt-3">
          <Panel>
            {congestion.error ? (
              <ErrorState message={congestion.error} onRetry={congestion.reload} />
            ) : congestion.data?.length ? (
              <DataTable>
                <thead>
                  <tr>
                    <th>Detected</th>
                    <th>Location</th>
                    <th>Severity</th>
                    <th>Recommended action</th>
                    <th className="!text-right">Duration</th>
                    <th className="!text-right">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {congestion.data.map((row) => (
                    <tr key={row.id}>
                      <td className="tnum whitespace-nowrap text-ink-2">{dateTime(row.detected_at)}</td>
                      <td className="whitespace-nowrap">
                        {row.intersection_id} {title(row.direction)}
                      </td>
                      <td>
                        <LevelBadge level={row.severity} />
                      </td>
                      <td className="max-w-80 truncate text-ink-2" title={row.recommended_action}>
                        {row.recommended_action}
                      </td>
                      <td className="tnum text-right">{duration(secondsBetween(row.detected_at, row.resolved_at))}</td>
                      <td className="text-right">
                        <Pill tone={row.status === "ACTIVE" ? "warn" : "neutral"}>{title(row.status)}</Pill>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
            ) : (
              <EmptyState message="No congestion recorded" />
            )}
          </Panel>
        </TabPanel>
      </Tabs>
    </>
  );
}
