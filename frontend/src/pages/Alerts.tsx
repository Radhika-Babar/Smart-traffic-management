import { useState } from "react";

import { DataTable, EmptyState, ErrorState, LoadingRows, PageHeader, Panel, Pill } from "@/components/common";
import { Button, Select } from "@/components/ui/primitives";
import { useAction, useFetch } from "@/hooks/useFetch";
import { api, query } from "@/services/api";
import { useLive } from "@/stores/live";
import type { Alert } from "@/types/api";
import { dateTime, title } from "@/utils/format";

const CATEGORIES = [
  "SEVERE_CONGESTION",
  "ACCIDENT_DETECTED",
  "SIMULATED_INCIDENT",
  "EMERGENCY_APPROACHING",
  "SENSOR_FAILURE",
  "MQTT_FAILURE",
  "SIGNAL_CONTROLLER_FAILURE",
  "UNUSUAL_TRAFFIC",
];
const PAGE = 25;

export function Alerts() {
  const version = useLive((s) => s.alertsVersion);
  const intersections = useLive((s) => s.live?.intersections) ?? [];
  const [category, setCategory] = useState("ALL");
  const [severity, setSeverity] = useState("ALL");
  const [site, setSite] = useState("ALL");
  const [state, setState] = useState("OPEN");
  const [page, setPage] = useState(0);
  const { run, busy } = useAction();

  const filters = {
    category: category === "ALL" ? null : category,
    severity: severity === "ALL" ? null : severity,
    intersection_id: site === "ALL" ? null : site,
    resolved: state === "OPEN" ? false : state === "RESOLVED" ? true : null,
    acknowledged: state === "UNACKNOWLEDGED" ? false : null,
    limit: PAGE,
    offset: page * PAGE,
  };
  const path = `/api/alerts${query(filters)}`;
  const alerts = useFetch(() => api.get<{ items: Alert[]; total: number }>(path), path, version);
  const total = alerts.data?.total ?? 0;

  const change = (setter: (value: string) => void) => (value: string) => {
    setter(value);
    setPage(0);
  };

  return (
    <>
      <PageHeader
        name="Alerts"
        finding={`${total} alert${total === 1 ? "" : "s"} match this view`}
        actions={
          <Button
            disabled={busy}
            onClick={async () => {
              await run(() => api.post("/api/alerts/acknowledge-all"), "All alerts acknowledged");
              alerts.reload();
            }}
          >
            Acknowledge all
          </Button>
        }
      />
      <Panel flush>
        <div className="flex flex-wrap gap-2 p-3">
          <Select
            label="Filter by state"
            className="w-40"
            value={state}
            onChange={change(setState)}
            options={[
              { value: "OPEN", label: "Open" },
              { value: "UNACKNOWLEDGED", label: "Unacknowledged" },
              { value: "RESOLVED", label: "Resolved" },
              { value: "ALL", label: "All states" },
            ]}
          />
          <Select
            label="Filter by category"
            className="w-52"
            value={category}
            onChange={change(setCategory)}
            options={[{ value: "ALL", label: "All categories" }, ...CATEGORIES.map((c) => ({ value: c, label: title(c) }))]}
          />
          <Select
            label="Filter by severity"
            className="w-32"
            value={severity}
            onChange={change(setSeverity)}
            options={[
              { value: "ALL", label: "Any severity" },
              { value: "CRITICAL", label: "Critical" },
              { value: "WARNING", label: "Warning" },
            ]}
          />
          <Select
            label="Filter by intersection"
            className="w-44"
            value={site}
            onChange={change(setSite)}
            options={[{ value: "ALL", label: "All intersections" }, ...intersections.map((i) => ({ value: i.id, label: i.name }))]}
          />
        </div>
        <div className="px-4 pb-3">
          {alerts.error ? (
            <ErrorState message={alerts.error} onRetry={alerts.reload} />
          ) : alerts.loading && !alerts.data ? (
            <LoadingRows rows={6} />
          ) : alerts.data?.items.length ? (
            <>
              <DataTable>
                <thead>
                  <tr>
                    <th>Severity</th>
                    <th>Alert</th>
                    <th>Category</th>
                    <th>Location</th>
                    <th>Raised</th>
                    <th className="!text-right">Acknowledgment</th>
                  </tr>
                </thead>
                <tbody>
                  {alerts.data.items.map((alert) => (
                    <tr key={alert.id}>
                      <td>
                        <Pill tone={alert.severity === "CRITICAL" ? "bad" : "warn"}>{title(alert.severity)}</Pill>
                      </td>
                      <td className="min-w-64">
                        <div>{alert.description}</div>
                        <div className="tnum text-xs text-ink-3">
                          {alert.id}
                          {alert.resolved ? ` · resolved ${dateTime(alert.resolved_at)}` : ""}
                          {alert.source === "SEEDED" ? " · seeded sample" : ""}
                        </div>
                      </td>
                      <td className="whitespace-nowrap text-ink-2">{title(alert.category)}</td>
                      <td className="whitespace-nowrap text-ink-2">{alert.intersection_id ?? "Network"}</td>
                      <td className="tnum whitespace-nowrap text-ink-2">{dateTime(alert.raised_at)}</td>
                      <td className="text-right">
                        {alert.acknowledged ? (
                          <span className="text-xs text-ink-3">Acknowledged</span>
                        ) : (
                          <Button
                            size="sm"
                            onClick={async () => {
                              await run(() => api.post(`/api/alerts/${alert.id}/acknowledge`));
                              alerts.reload();
                            }}
                          >
                            Acknowledge
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
              <div className="tnum mt-2 flex items-center justify-end gap-2 text-xs text-ink-3">
                {page * PAGE + 1}–{Math.min(total, (page + 1) * PAGE)} of {total}
                <Button size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>
                  Previous
                </Button>
                <Button size="sm" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>
                  Next
                </Button>
              </div>
            </>
          ) : (
            <EmptyState message="No alerts in this view" detail="Open alerts appear here the moment they are raised" />
          )}
        </div>
      </Panel>
    </>
  );
}
