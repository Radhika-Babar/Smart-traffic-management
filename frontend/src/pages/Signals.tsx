import { useEffect, useState } from "react";

import { DataTable, EmptyState, ErrorState, Field, LevelBadge, LoadingRows, PageHeader, Panel, Pill, SignalLamp } from "@/components/common";
import { DecisionExplainer } from "@/components/DecisionExplainer";
import { phaseText, signalId, SignalControls } from "@/components/SignalControls";
import { Button, Input } from "@/components/ui/primitives";
import { useAction, useFetch } from "@/hooks/useFetch";
import { api } from "@/services/api";
import { useLive } from "@/stores/live";
import type { DecisionRecord, IntersectionLive, SignalConfig, SignalHistory } from "@/types/api";
import { clock, cn, DIRECTIONS, duration, fixed, title } from "@/utils/format";

const TIMING_FIELDS: { key: keyof SignalConfig; label: string }[] = [
  { key: "min_green_s", label: "Minimum green" },
  { key: "max_green_s", label: "Maximum green" },
  { key: "default_green_s", label: "Default green" },
  { key: "yellow_s", label: "Yellow" },
  { key: "all_red_s", label: "All-red clearance" },
  { key: "preempt_max_hold_s", label: "Priority hold limit" },
];

function Timing({ id }: { id: string }) {
  const config = useFetch(() => api.get<SignalConfig>(`/api/signals/${id}`), id);
  const [form, setForm] = useState<Record<string, string>>({});
  const { run, busy } = useAction();

  useEffect(() => {
    if (config.data) {
      setForm(Object.fromEntries(TIMING_FIELDS.map((f) => [f.key, String(config.data![f.key])])));
    }
  }, [config.data]);

  if (config.error) return <ErrorState message={config.error} onRetry={config.reload} />;
  if (!config.data) return <LoadingRows rows={3} />;
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        run(
          () => api.put(`/api/signals/${id}/timing`, Object.fromEntries(Object.entries(form).map(([k, v]) => [k, Number(v)]))),
          "Timing applied to the field controller",
        );
      }}
    >
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {TIMING_FIELDS.map((field) => (
          <Field key={field.key} label={`${field.label} (s)`}>
            <Input
              type="number"
              step="0.5"
              value={form[field.key] ?? ""}
              onChange={(event) => setForm({ ...form, [field.key]: event.target.value })}
            />
          </Field>
        ))}
      </div>
      <div className="mt-3 flex justify-end">
        <Button type="submit" disabled={busy}>
          Apply timing
        </Button>
      </div>
    </form>
  );
}

function Selected({ item }: { item: IntersectionLive }) {
  const id = signalId(item.id);
  const serial = item.last_decision?.decided_at ?? "";
  const decisions = useFetch(() => api.get<DecisionRecord[]>(`/api/signals/${id}/decisions?limit=8`), `${id}${serial}`);
  const history = useFetch(() => api.get<SignalHistory[]>(`/api/signals/${id}/history?limit=8`), `h${id}${serial}`);

  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <Panel name="Why this direction" hint="Live weighted priority for each approach, computed from sensor readings received over MQTT">
        <DecisionExplainer scores={item.scores} decision={item.last_decision} active={item.active_direction} />
      </Panel>
      <div className="flex flex-col gap-3">
        <Panel name="Control">
          <SignalControls item={item} />
        </Panel>
        <Panel name="Timing parameters" hint="Enforced by the field controller. Values that break the safety rules are rejected.">
          <Timing id={id} />
        </Panel>
      </div>
      <Panel name="Decision log">
        {decisions.error ? (
          <ErrorState message={decisions.error} onRetry={decisions.reload} />
        ) : decisions.loading && !decisions.data ? (
          <LoadingRows />
        ) : decisions.data?.length ? (
          <ul>
            {decisions.data.map((row) => (
              <li key={row.id} className="flex items-start gap-2 border-t border-line py-1.5 first:border-t-0">
                <span className="tnum shrink-0 text-xs leading-5 text-ink-3">{clock(row.decided_at)}</span>
                <span className="min-w-0 flex-1 text-[13px] text-ink-2">{row.reason}</span>
                <span className="tnum shrink-0 text-xs leading-5">{fixed(row.green_s)} s</span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState message="No decisions recorded yet" />
        )}
      </Panel>
      <Panel name="Phase history">
        {history.data?.length ? (
          <DataTable>
            <thead>
              <tr>
                <th>Started</th>
                <th>Phase</th>
                <th>Ended because</th>
                <th className="!text-right">Duration</th>
              </tr>
            </thead>
            <tbody>
              {history.data.map((row) => (
                <tr key={row.id}>
                  <td className="tnum whitespace-nowrap text-ink-2">{clock(row.started_at)}</td>
                  <td className="whitespace-nowrap">
                    {title(row.phase)} {row.direction ? title(row.direction) : ""}
                  </td>
                  <td className="max-w-48 truncate text-ink-2" title={row.end_reason ?? ""}>
                    {row.end_reason || "–"}
                  </td>
                  <td className="tnum text-right">{duration(row.duration_s)}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        ) : (
          <EmptyState message="No completed phases yet" />
        )}
      </Panel>
    </div>
  );
}

export function Signals() {
  const intersections = useLive((s) => s.live?.intersections);
  const strategy = useLive((s) => s.live?.simulation.control_strategy);
  const [selected, setSelected] = useState("INT-001");
  if (!intersections) {
    return (
      <>
        <PageHeader name="Signal Control" />
        <LoadingRows rows={8} />
      </>
    );
  }
  const item = intersections.find((i) => i.id === selected) ?? intersections[0];
  const manual = intersections.filter((i) => i.mode === "MANUAL").length;
  return (
    <>
      <PageHeader
        name="Signal Control"
        finding={`${intersections.length - manual} of ${intersections.length} signals automatic · strategy ${title(strategy ?? "ADAPTIVE")}`}
      />
      <Panel flush className="mb-3">
        <div className="grid sm:grid-cols-2 xl:grid-cols-3">
          {intersections.map((entry) => (
            <button
              key={entry.id}
              onClick={() => setSelected(entry.id)}
              className={cn(
                "flex items-center gap-3 border-b border-line px-4 py-2 text-left transition-colors hover:bg-card-hover",
                entry.id === item.id && "bg-card-hover",
              )}
            >
              <span className="grid grid-cols-2 gap-0.5">
                {DIRECTIONS.map((d) => (
                  <SignalLamp key={d} aspect={entry.heads[d]} label={`${d} ${entry.heads[d] ?? "off"}`} />
                ))}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium">{entry.name}</span>
                <span className="tnum block truncate text-xs text-ink-3">
                  {phaseText(entry)}
                  {entry.phase === "GREEN" ? ` · ${fixed(entry.remaining)} s left` : ""}
                </span>
              </span>
              {entry.mode === "MANUAL" ? <Pill tone="info">Manual</Pill> : <LevelBadge level={entry.congestion} />}
            </button>
          ))}
        </div>
      </Panel>
      <Selected item={item} />
    </>
  );
}
