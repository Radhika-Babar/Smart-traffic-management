import { Download } from "lucide-react";
import { useMemo, useState } from "react";

import { Columns, Legend, RankBars, TimeChart } from "@/charts";
import { DataTable, EmptyState, ErrorState, Field, LoadingRows, Metric, PageHeader, Panel } from "@/components/common";
import { Button, Input, Select } from "@/components/ui/primitives";
import { useFetch } from "@/hooks/useFetch";
import { api, query } from "@/services/api";
import { useLive } from "@/stores/live";
import type { Analytics as Report } from "@/types/api";
import { DIRECTION_COLOR, DIRECTIONS, dateTime, fixed, LEVEL_COLOR, title } from "@/utils/format";

function day(offset: number): string {
  const moment = new Date(Date.now() + offset * 86400000);
  return moment.toISOString().slice(0, 10);
}

export function Analytics() {
  const intersections = useLive((s) => s.live?.intersections) ?? [];
  const [start, setStart] = useState(day(-7));
  const [end, setEnd] = useState(day(1));
  const [site, setSite] = useState("ALL");
  const [direction, setDirection] = useState("ALL");
  const [condition, setCondition] = useState("ALL");

  const params = query({
    start: `${start}T00:00:00`,
    end: `${end}T00:00:00`,
    intersection_id: site === "ALL" ? null : site,
    direction: direction === "ALL" ? null : direction,
    condition: condition === "ALL" ? null : condition,
  });
  const report = useFetch(() => api.get<Report>(`/api/analytics/traffic${params}`), params);
  const data = report.data;

  const signal = useMemo(() => {
    const rows = data?.signal_utilization ?? [];
    const merged = new Map<string, Record<string, number | string>>();
    for (const row of rows) {
      const entry = merged.get(row.intersection_id) ?? { id: row.intersection_id };
      entry[row.direction] = row.share;
      merged.set(row.intersection_id, entry);
    }
    return [...merged.values()];
  }, [data]);

  return (
    <>
      <PageHeader
        name="Analytics"
        finding={
          data?.summary
            ? `${fixed(data.summary.volume)} vehicles counted, ${fixed(data.summary.congested_pct, 1)}% of approach time congested`
            : "Stored traffic history"
        }
        actions={
          <Button asChild>
            <a href={`/api/analytics/export${params}`} download>
              <Download /> Export CSV
            </a>
          </Button>
        }
      />
      <section aria-label="Filters" className="mb-3 flex flex-wrap items-end gap-2 rounded-lg border border-line bg-card p-3">
        <Field label="From">
          <Input type="date" className="w-36" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
        </Field>
        <Field label="To">
          <Input type="date" className="w-36" value={end} min={start} onChange={(e) => setEnd(e.target.value)} />
        </Field>
        <Field label="Intersection">
          <Select label="Intersection" className="w-44" value={site} onChange={setSite} options={[{ value: "ALL", label: "All intersections" }, ...intersections.map((i) => ({ value: i.id, label: i.name }))]} />
        </Field>
        <Field label="Road direction">
          <Select label="Road direction" className="w-32" value={direction} onChange={setDirection} options={[{ value: "ALL", label: "All" }, ...DIRECTIONS.map((d) => ({ value: d, label: title(d) }))]} />
        </Field>
        <Field label="Traffic condition">
          <Select
            label="Traffic condition"
            className="w-32"
            value={condition}
            onChange={setCondition}
            options={[{ value: "ALL", label: "All" }, ...["LOW", "MODERATE", "HIGH", "SEVERE"].map((l) => ({ value: l, label: title(l) }))]}
          />
        </Field>
        {data ? (
          <p className="tnum ml-auto self-center text-xs text-ink-3">
            {fixed(data.sources.LIVE ?? 0)} live and {fixed(data.sources.SEEDED ?? 0)} seeded rows
          </p>
        ) : null}
      </section>

      {report.error ? (
        <Panel>
          <ErrorState message={report.error} onRetry={report.reload} />
        </Panel>
      ) : report.loading && !data ? (
        <LoadingRows rows={10} />
      ) : !data?.summary ? (
        <Panel>
          <EmptyState message="No traffic records match these filters" detail="Widen the date range or clear a filter" />
        </Panel>
      ) : (
        <>
          <section aria-label="Summary" className="mb-3 grid grid-cols-2 divide-line rounded-lg border border-line bg-card md:grid-cols-4 md:divide-x">
            <Metric label="Traffic volume" value={fixed(data.summary.volume)} unit="vehicles" />
            <Metric label="Average waiting time" value={fixed(data.summary.wait, 1)} unit="s" />
            <Metric label="Average vehicle speed" value={fixed((data.summary.speed ?? 0) * 3.6, 1)} unit="km/h" />
            <Metric label="Average density" value={fixed(data.summary.density, 1)} unit="%" />
          </section>

          <div className="mb-3 grid gap-3 xl:grid-cols-2">
            <Panel name="Traffic volume over time">
              <TimeChart data={data.series} x="t" series={[{ key: "volume", name: "Volume", color: "#3987e5" }]} format={dateTime} unit="vehicles" height={200} />
            </Panel>
            <Panel name="Hourly traffic volume" hint="Mean vehicles per hour of day across the selected dates, on the machine's local time">
              <Columns data={data.hourly_profile} x="hour" y="volume" name="Volume" unit="vehicles" height={200} format={(hour) => `${hour}:00`} />
            </Panel>
            <Panel name="Daily traffic trend">
              <Columns data={data.daily} x="date" y="volume" name="Volume" unit="vehicles" height={180} format={(date) => date.slice(5)} />
            </Panel>
            <Panel name="Waiting time by hour of day" hint="Mean wait for each hour of the day across the selected dates">
              <TimeChart data={data.hourly_profile} x="hour" series={[{ key: "wait", name: "Waiting time", color: "#3987e5" }]} format={(hour) => `${hour}:00`} unit="s" height={180} />
            </Panel>
          </div>

          <Panel name="Intersection performance" className="mb-3">
            <DataTable>
              <thead>
                <tr>
                  <th>Intersection</th>
                  <th className="!text-right">Volume</th>
                  <th className="!text-right">Wait</th>
                  <th className="!text-right">Speed</th>
                  <th className="!text-right">Density</th>
                  <th className="!text-right">Time congested</th>
                  <th className="!text-right">Congestion events</th>
                </tr>
              </thead>
              <tbody>
                {data.by_intersection.map((row) => {
                  const events = data.congestion_events.find((e) => e.id === row.id);
                  return (
                    <tr key={row.id}>
                      <td className="font-medium">{row.name}</td>
                      <td className="tnum text-right">{fixed(row.volume)}</td>
                      <td className="tnum text-right">{fixed(row.wait, 1)} s</td>
                      <td className="tnum text-right">{fixed((row.speed ?? 0) * 3.6, 1)} km/h</td>
                      <td className="tnum text-right">{fixed(row.density, 1)}%</td>
                      <td className="tnum text-right">{fixed(row.congested_pct, 1)}%</td>
                      <td className="tnum text-right">{events ? events.HIGH + events.SEVERE : 0}</td>
                    </tr>
                  );
                })}
              </tbody>
            </DataTable>
          </Panel>

          <div className="grid gap-3 xl:grid-cols-3">
            <Panel name="Congestion frequency" hint="Share of recorded approach time at each level">
              <div className="space-y-1.5">
                {data.congestion_levels.map((row) => (
                  <RankBars
                    key={row.level}
                    rows={[{ key: row.level, label: title(row.level), value: row.share }]}
                    max={100}
                    unit="%"
                    format={(value) => fixed(value, 1)}
                    color={LEVEL_COLOR[row.level]}
                  />
                ))}
              </div>
            </Panel>
            <Panel name="Sensor uptime" hint="Readings delivered against readings expected, over the current run up to one hour">
              <RankBars
                rows={data.sensor_uptime.map((row) => ({ key: row.id, label: row.name, value: row.uptime }))}
                max={100}
                unit="%"
                format={(value) => fixed(value, 1)}
              />
            </Panel>
            <Panel name="Emergency vehicle response">
              {data.emergency.completed ? (
                <div className="grid grid-cols-2">
                  <Metric label="Completed" value={data.emergency.completed} />
                  <Metric label="Average travel" value={fixed(data.emergency.avg_travel_s)} unit="s" />
                  <Metric label="Average stopped" value={fixed(data.emergency.avg_wait_s)} unit="s" />
                  <Metric label="Priority requests" value={fixed(data.emergency.avg_preemptions, 1)} unit="each" />
                </div>
              ) : (
                <EmptyState message="No completed runs in this range" detail="Dispatch a vehicle to record response times" />
              )}
            </Panel>
            <Panel
              name="Signal utilization"
              className="xl:col-span-3"
              hint="Share of green time given to each approach, from recorded phase history"
              actions={<Legend items={DIRECTIONS.map((d) => ({ name: title(d), color: DIRECTION_COLOR[d] }))} />}
            >
              {signal.length ? (
                <ul className="space-y-1.5">
                  {signal.map((row) => (
                    <li key={String(row.id)} className="flex items-center gap-3 text-[13px]">
                      <span className="w-16 shrink-0 text-ink-2">{String(row.id)}</span>
                      <div className="flex h-3 flex-1 gap-0.5 overflow-hidden rounded-sm">
                        {DIRECTIONS.map((d) => (
                          <div key={d} title={`${title(d)} ${fixed(Number(row[d] ?? 0), 1)}%`} style={{ width: `${row[d] ?? 0}%`, background: DIRECTION_COLOR[d] }} />
                        ))}
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState message="No signal phases recorded in this range" />
              )}
            </Panel>
          </div>
        </>
      )}
    </>
  );
}
