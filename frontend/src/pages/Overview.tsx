import { Link } from "react-router-dom";

import { Columns, TimeChart } from "@/charts";
import {
  EmptyState,
  LevelBadge,
  LoadingRows,
  Metric,
  PageHeader,
  Panel,
  Pill,
  SignalLamp,
} from "@/components/common";
import { CityMap } from "@/components/map/CityMap";
import { phaseText } from "@/components/SignalControls";
import { Button } from "@/components/ui/primitives";
import { useFetch } from "@/hooks/useFetch";
import { api } from "@/services/api";
import { useLive } from "@/stores/live";
import type { IntersectionLive, Level, SystemEvent } from "@/types/api";
import { clock, DIRECTIONS, fixed, LEVEL_COLOR, shortTime, title } from "@/utils/format";

function finding(intersections: IntersectionLive[]): string {
  const ranked = [...intersections].sort(
    (a, b) => b.approaches.reduce((s, x) => s + x.queue, 0) - a.approaches.reduce((s, x) => s + x.queue, 0),
  );
  const worst = ranked[0];
  if (!worst) return "Waiting for the first sensor readings";
  const approach = [...worst.approaches].sort((a, b) => b.queue - a.queue)[0];
  if (!approach || approach.queue < 1) return "No queues on any approach right now";
  return `Longest queue: ${fixed(approach.queue)} vehicles on ${title(approach.direction)} at ${worst.name}, waiting ${fixed(approach.avg_wait_s)} s`;
}

function Heatmap({ intersections }: { intersections: IntersectionLive[] }) {
  return (
    <table className="tnum w-full text-xs">
      <thead>
        <tr className="text-ink-3">
          <th className="pb-1 text-left font-normal">Occupancy %</th>
          {DIRECTIONS.map((d) => (
            <th key={d} className="pb-1 font-normal">
              {title(d)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {intersections.map((item) => (
          <tr key={item.id}>
            <td className="truncate pr-2 text-[13px] text-ink-2">{item.name}</td>
            {DIRECTIONS.map((direction) => {
              const approach = item.approaches.find((a) => a.direction === direction);
              const level: Level = approach?.level ?? "LOW";
              return (
                <td key={direction} className="p-px">
                  <div
                    title={approach ? `${title(level)} congestion` : "Disabled"}
                    className="flex h-6 items-center justify-center rounded-sm text-[12px]"
                    style={{
                      background: approach
                        ? `color-mix(in srgb, ${LEVEL_COLOR[level]} ${12 + approach.occupancy * 0.5}%, #111827)`
                        : "#111827",
                    }}
                  >
                    {approach ? fixed(approach.occupancy) : "–"}
                  </div>
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function EventFeed({ events }: { events: SystemEvent[] }) {
  if (!events.length) return <EmptyState message="No events yet" />;
  return (
    <ul>
      {events.slice(0, 6).map((event, index) => (
        <li key={`${event.occurred_at}-${index}`} className="flex gap-3 border-t border-line py-1.5 first:border-t-0">
          <span className="tnum shrink-0 text-xs leading-5 text-ink-3">{clock(event.occurred_at)}</span>
          <span className="min-w-0 truncate text-[13px] text-ink-2" title={event.message}>
            {event.message}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function Overview() {
  const live = useLive((s) => s.live);
  const series = useLive((s) => s.series);
  const liveEvents = useLive((s) => s.events);
  const stored = useFetch(() => api.get<SystemEvent[]>("/api/dashboard/events?limit=8"), "events");
  const events = liveEvents.length >= 6 ? liveEvents : [...liveEvents, ...(stored.data ?? [])];

  if (!live) {
    return (
      <>
        <PageHeader name="Overview" finding="Connecting to the traffic network" />
        <LoadingRows rows={8} />
      </>
    );
  }
  const { overview, intersections, alerts } = live;
  const health = overview.network_health;
  const healthTone = health.status === "HEALTHY" ? "good" : health.status === "DEGRADED" ? "warn" : "bad";
  const performance = intersections.map((item) => ({
    name: item.name.split(" ")[0],
    queue: item.approaches.reduce((sum, a) => sum + a.queue, 0),
  }));

  return (
    <>
      <PageHeader
        name="Overview"
        finding={finding(intersections)}
        actions={
          <Button asChild variant="primary">
            <Link to="/map">Open live map</Link>
          </Button>
        }
      />

      <section
        aria-label="Network metrics"
        className="mb-3 grid grid-cols-2 divide-line rounded-lg border border-line bg-card md:grid-cols-5 md:divide-x"
      >
        <Metric label="Vehicles in network" value={fixed(overview.total_vehicles)} sub={`${fixed(overview.completed_trips)} trips completed`} />
        <Metric label="Average density" value={fixed(overview.average_density, 1)} unit="%" hint="Mean of the density sensors across all approaches" />
        <Metric label="Average wait" value={fixed(overview.average_wait_s, 1)} unit="s" hint="Mean wait of queued vehicles, from detection sensors" />
        <Metric
          label="Flow efficiency"
          value={fixed(overview.flow_efficiency, 1, "–")}
          unit={overview.flow_efficiency === null ? undefined : "%"}
          hint="Free-flow travel time divided by actual travel time, over the last 300 completed trips"
        />
        <Metric
          label="Network health"
          value={fixed(health.score, 0)}
          unit="/100"
          tone={healthTone}
          sub={title(health.status)}
          hint="Weighted from sensors online (40%), controllers online (30%) and MQTT transport (30%)"
        />
        <Metric label="Intersections" value={overview.total_intersections} sub={`${overview.auto_signals} of ${overview.total_intersections} on automatic`} />
        <Metric
          label="Virtual sensors active"
          value={overview.active_sensors}
          unit={`of ${overview.total_sensors}`}
          tone={overview.offline_sensors ? "warn" : undefined}
          sub={overview.offline_sensors ? `${overview.offline_sensors} offline` : "All reporting"}
        />
        <Metric label="Congestion alerts" value={overview.active_congestion_alerts} tone={overview.active_congestion_alerts ? "warn" : undefined} sub={`${overview.active_alerts} alerts open in total`} />
        <Metric label="Emergency vehicles" value={overview.active_emergencies} tone={overview.active_emergencies ? "bad" : undefined} sub="En route" />
        <Metric label="Network flow" value={fixed(overview.flow_vph)} unit="veh/h" hint="Stop-line counts in the last sensor interval, scaled to an hour" />
      </section>

      <div className="mb-3 grid gap-3 xl:grid-cols-3">
        <Panel name="Traffic density" hint="Network average from density sensors, sampled every 5 simulated seconds" actions="Last 20 min">
          <TimeChart data={series} x="t" series={[{ key: "density", name: "Density", color: "#3987e5" }]} format={shortTime} unit="%" area height={170} />
        </Panel>
        <Panel name="Traffic volume" hint="Vehicles crossing stop lines, scaled to an hourly rate" actions="Last 20 min">
          <TimeChart data={series} x="t" series={[{ key: "volume", name: "Volume", color: "#3987e5" }]} format={shortTime} unit="veh/h" height={170} />
        </Panel>
        <Panel name="Queue by intersection" hint="Queued vehicles right now, from detection sensors">
          <Columns data={performance} x="name" y="queue" name="Queue" unit="vehicles" height={170} />
        </Panel>
      </div>

      <div className="mb-3 grid gap-3 xl:grid-cols-[minmax(0,5fr)_minmax(0,4fr)_minmax(0,4fr)]">
        <Panel name="Signal status" flush>
          <ul className="px-4 pb-2">
            {intersections.map((item) => (
              <li key={item.id} className="flex h-9 items-center gap-2 border-t border-line text-[13px] first:border-t-0">
                <span className="flex gap-0.5">
                  {DIRECTIONS.map((d) => (
                    <SignalLamp key={d} aspect={item.heads[d]} label={`${d} ${item.heads[d] ?? "off"}`} />
                  ))}
                </span>
                <span className="min-w-0 flex-1 truncate">{item.name}</span>
                <span className="hidden truncate text-xs text-ink-3 sm:block">{phaseText(item)}</span>
                <LevelBadge level={item.congestion} />
                {item.preempt_vehicle ? <Pill tone="bad">Priority</Pill> : null}
                {item.mode === "MANUAL" ? <Pill tone="info">Manual</Pill> : null}
              </li>
            ))}
          </ul>
        </Panel>
        <Panel name="Congestion heatmap" hint="Stop-line occupancy, tinted by each approach's congestion level">
          <Heatmap intersections={intersections} />
        </Panel>
        <Panel name="City now" flush className="overflow-hidden">
          <CityMap interactive={false} className="h-[228px] rounded-b-lg" />
        </Panel>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel
          name="Active alerts"
          actions={
            <Link to="/alerts" className="text-ink-2 hover:text-ink">
              View all
            </Link>
          }
        >
          {alerts.length ? (
            <ul>
              {alerts.slice(0, 6).map((alert) => (
                <li key={alert.id} className="flex items-center gap-2 border-t border-line py-1.5 first:border-t-0">
                  <Pill tone={alert.severity === "CRITICAL" ? "bad" : "warn"}>{title(alert.severity)}</Pill>
                  <span className="min-w-0 flex-1 truncate text-[13px]" title={alert.description}>
                    {alert.description}
                  </span>
                  <span className="tnum shrink-0 text-xs text-ink-3">{shortTime(alert.raised_at)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState message="No active alerts" detail="Every sensor, controller and approach is within its limits" />
          )}
        </Panel>
        <Panel name="Recent system events">
          <EventFeed events={events} />
        </Panel>
      </div>
    </>
  );
}
