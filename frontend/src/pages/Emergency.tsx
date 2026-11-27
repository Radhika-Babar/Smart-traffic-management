import { useEffect, useState } from "react";

import { DataTable, EmptyState, ErrorState, Field, LoadingRows, PageHeader, Panel, Pill } from "@/components/common";
import { CityMap } from "@/components/map/CityMap";
import { Button, Select } from "@/components/ui/primitives";
import { useAction, useFetch } from "@/hooks/useFetch";
import { api, query } from "@/services/api";
import { useLive } from "@/stores/live";
import type { EmergencyLive, EmergencyRecord } from "@/types/api";
import { clock, duration, fixed, title } from "@/utils/format";

const TYPES = ["AMBULANCE", "FIRE_ENGINE", "POLICE"];

function Tracker({ vehicle }: { vehicle: EmergencyLive }) {
  const { run, busy } = useAction();
  return (
    <div className="border-t border-line py-2 first:border-t-0">
      <div className="flex items-center gap-2">
        <span className="tnum text-[13px] font-medium">{vehicle.vehicle_id}</span>
        <Pill tone="bad">{title(vehicle.vehicle_type)}</Pill>
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          disabled={busy}
          onClick={() => run(() => api.delete(`/api/emergency-vehicles/${vehicle.vehicle_id}`), "Vehicle recalled, priority released")}
        >
          Cancel
        </Button>
      </div>
      <ol className="mt-1.5 flex flex-wrap items-center gap-1 text-xs">
        {vehicle.route.map((stop, index) => (
          <li key={stop} className="flex items-center gap-1">
            {index ? <span className="text-ink-3">›</span> : null}
            <span className={index < vehicle.route_index ? "text-ink-3" : index === vehicle.route_index ? "font-medium text-ink" : "text-ink-2"}>
              {stop}
            </span>
          </li>
        ))}
      </ol>
      <div className="tnum mt-1 text-xs text-ink-3">
        {vehicle.next_intersection
          ? `${fixed(vehicle.distance_m)} m to ${vehicle.next_intersection} from ${title(vehicle.approach ?? "")} · ETA ${fixed(vehicle.eta_s)} s · ${fixed(vehicle.speed * 3.6)} km/h`
          : "Clearing the last intersection"}
        {` · ${vehicle.preemptions} priority requests`}
      </div>
    </div>
  );
}

export function Emergency() {
  const live = useLive((s) => s.live);
  const version = useLive((s) => s.emergencyVersion);
  const intersections = live?.intersections ?? [];
  const [type, setType] = useState("AMBULANCE");
  const [origin, setOrigin] = useState("INT-001");
  const [destination, setDestination] = useState("INT-006");
  const [route, setRoute] = useState<string[]>([]);
  const { run, busy } = useAction();
  const history = useFetch(() => api.get<EmergencyRecord[]>("/api/emergency-vehicles?limit=12"), "history", version);
  const active = live?.emergencies ?? [];
  const sameStop = origin === destination;

  useEffect(() => {
    if (sameStop) {
      setRoute([]);
      return;
    }
    api
      .get<{ route: string[] }>(`/api/emergency-vehicles/route${query({ origin, destination })}`)
      .then((result) => setRoute(result.route))
      .catch(() => setRoute([]));
  }, [origin, destination, sameStop]);

  const options = intersections.map((i) => ({ value: i.id, label: i.name }));
  return (
    <>
      <PageHeader
        name="Emergency Management"
        finding={active.length ? `${active.length} vehicle${active.length > 1 ? "s" : ""} en route with signal priority` : "No emergency vehicles on the network"}
      />
      <div className="mb-3 grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <div className="flex flex-col gap-3">
          <Panel name="Dispatch simulated vehicle">
            <div className="space-y-2.5">
              <Field label="Vehicle">
                <Select label="Vehicle type" className="w-full" value={type} onChange={setType} options={TYPES.map((t) => ({ value: t, label: title(t) }))} />
              </Field>
              <div className="grid grid-cols-2 gap-2">
                <Field label="From">
                  <Select label="Origin" className="w-full" value={origin} onChange={setOrigin} options={options} />
                </Field>
                <Field label="To">
                  <Select label="Destination" className="w-full" value={destination} onChange={setDestination} options={options} />
                </Field>
              </div>
              <p className="text-xs text-ink-3">
                {sameStop ? "Choose two different intersections" : route.length ? `Shortest route: ${route.join(" › ")}` : "Finding route"}
              </p>
              <Button
                variant="primary"
                className="w-full"
                disabled={busy || sameStop}
                onClick={() =>
                  run(
                    () => api.post("/api/emergency-vehicles", { vehicle_type: type, origin, destination }),
                    "Vehicle dispatched",
                  )
                }
              >
                Dispatch {title(type).toLowerCase()}
              </Button>
            </div>
          </Panel>
          <Panel name="Active vehicles">
            {active.length ? (
              active.map((vehicle) => <Tracker key={vehicle.vehicle_id} vehicle={vehicle} />)
            ) : (
              <EmptyState message="None en route" detail="Dispatched vehicles are tracked here live" />
            )}
          </Panel>
        </div>
        <Panel name="Priority corridor" flush className="overflow-hidden" hint="Each intersection clears through yellow and all-red before giving the vehicle green, then returns to adaptive control after it passes.">
          <CityMap className="h-[440px] rounded-b-lg" />
        </Panel>
      </div>
      <Panel name="Response log">
        {history.error ? (
          <ErrorState message={history.error} onRetry={history.reload} />
        ) : history.loading && !history.data ? (
          <LoadingRows />
        ) : history.data?.length ? (
          <DataTable>
            <thead>
              <tr>
                <th>Vehicle</th>
                <th>Type</th>
                <th>Route</th>
                <th>Dispatched</th>
                <th className="!text-right">Travel</th>
                <th className="!text-right">Stopped</th>
                <th className="!text-right">Priority requests</th>
                <th className="!text-right">Status</th>
              </tr>
            </thead>
            <tbody>
              {history.data.map((row) => (
                <tr key={row.id}>
                  <td className="tnum font-medium">{row.id}</td>
                  <td className="text-ink-2">{title(row.vehicle_type)}</td>
                  <td className="whitespace-nowrap text-ink-2">
                    {row.origin_id} › {row.destination_id}
                  </td>
                  <td className="tnum text-ink-2">{clock(row.dispatched_at)}</td>
                  <td className="tnum text-right">{duration(row.travel_time_s)}</td>
                  <td className="tnum text-right">{duration(row.wait_time_s)}</td>
                  <td className="tnum text-right">{row.preemptions}</td>
                  <td className="text-right">
                    <Pill tone={row.status === "ARRIVED" ? "good" : row.status === "EN_ROUTE" ? "bad" : "neutral"}>{title(row.status)}</Pill>
                  </td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : (
          <EmptyState message="No vehicles dispatched yet" />
        )}
      </Panel>
    </>
  );
}
