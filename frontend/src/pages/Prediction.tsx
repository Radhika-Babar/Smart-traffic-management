import { useEffect, useRef, useState } from "react";

import { Legend, RankBars, TimeChart } from "@/charts";
import { DataTable, EmptyState, ErrorState, LevelBadge, LoadingRows, Metric, PageHeader, Panel, Pill } from "@/components/common";
import { Button, Select } from "@/components/ui/primitives";
import { useAction, useFetch } from "@/hooks/useFetch";
import { api, query } from "@/services/api";
import { useLive } from "@/stores/live";
import type { BacktestPoint, Evaluation, Prediction as Forecast } from "@/types/api";
import { clock, dateTime, fixed, shortTime, title } from "@/utils/format";

const TARGETS = [
  { value: "volume", label: "Traffic volume", unit: "veh / 5 min" },
  { value: "wait", label: "Waiting time", unit: "s" },
  { value: "density", label: "Traffic density", unit: "%" },
];

const FEATURE_LABELS: Record<string, string> = {
  hour_sin: "Time of day (sine)",
  hour_cos: "Time of day (cosine)",
  weekday: "Day of week",
  is_weekend: "Weekend",
  site: "Intersection",
  volume_mean_1h: "Volume, hourly mean",
};

function featureLabel(name: string): string {
  if (FEATURE_LABELS[name]) return FEATURE_LABELS[name];
  const [metric, ...rest] = name.split("_");
  const tail = rest.join(" ");
  if (tail === "now") return `${title(metric)}, current`;
  const lag = Number(tail.replace("lag", ""));
  return `${title(metric)}, ${lag * 5} min ago`;
}

export function Prediction() {
  const intersections = useLive((s) => s.live?.intersections) ?? [];
  const clockNow = useLive((s) => s.live?.simulation.clock);
  const [site, setSite] = useState("INT-002");
  const [target, setTarget] = useState("volume");
  const evaluation = useFetch(() => api.get<Evaluation>("/api/predictions/evaluation"), "evaluation");
  const backtest = useFetch(
    () => api.get<BacktestPoint[]>(`/api/predictions/backtest${query({ intersection_id: site })}`),
    `backtest${site}${evaluation.data?.run_id}`,
  );
  const bucket = clockNow ? Math.floor(new Date(clockNow).getTime() / 300000) : 0;
  const forecasts = useFetch(
    () => api.get<Forecast[]>(`/api/predictions${query({ intersection_id: site, limit: 24 })}`),
    `forecast${site}${bucket}`,
  );
  const { run, busy } = useAction();
  const reloadEvaluation = useRef(evaluation.reload);
  reloadEvaluation.current = evaluation.reload;
  const training = evaluation.data?.training;

  useEffect(() => {
    if (!training) return;
    const timer = setInterval(() => reloadEvaluation.current(), 2000);
    return () => clearInterval(timer);
  }, [training]);

  const info = evaluation.data;
  const unit = TARGETS.find((t) => t.value === target)!.unit;
  const metrics = info?.metrics.filter((m) => m.target === target) ?? [];
  const selected = metrics.find((m) => m.selected);
  const baseline = metrics.find((m) => m.model_name === "Persistence");
  const gain = selected && baseline && baseline.rmse > 0 ? (100 * (baseline.rmse - selected.rmse)) / baseline.rmse : null;
  const forest = metrics.find((m) => m.feature_importance);
  const importance = Object.entries(forest?.feature_importance ?? {}).slice(0, 6);
  const latest = forecasts.data?.[0];
  const compared = [...(forecasts.data ?? [])].reverse().filter((row) => row.actual_volume !== null);

  return (
    <>
      <PageHeader
        name="Traffic Prediction"
        finding={
          info?.trained
            ? `${selected?.model_name ?? "Model"} forecasts the next 5 minutes, ${fixed(gain, 1)}% lower error than persistence on the hold-out`
            : info?.last_error ?? "Models are training on locally generated history"
        }
        actions={
          <>
            <Select label="Intersection" className="w-44" value={site} onChange={setSite} options={intersections.map((i) => ({ value: i.id, label: i.name }))} />
            <Select label="Prediction target" className="w-40" value={target} onChange={setTarget} options={TARGETS} />
            <Button
              disabled={busy || training}
              onClick={async () => {
                await run(() => api.post("/api/predictions/train"), "Training started");
                evaluation.reload();
              }}
            >
              {training ? "Training" : "Retrain models"}
            </Button>
          </>
        }
      />

      <section aria-label="Latest forecast" className="mb-3 grid grid-cols-2 divide-line rounded-lg border border-line bg-card md:grid-cols-4 md:divide-x">
        <Metric label="Next 5 min volume" value={fixed(latest?.predicted_volume)} unit="vehicles" sub={latest ? `For ${shortTime(latest.target_at)}` : "No forecast yet"} />
        <Metric label="Expected wait" value={fixed(latest?.predicted_wait_s)} unit="s" />
        <Metric label="Expected density" value={fixed(latest?.predicted_density)} unit="%" />
        <Metric label="Expected congestion" value={latest ? <LevelBadge level={latest.predicted_level} /> : "–"} sub={latest ? `Model: ${latest.model_name}` : undefined} />
      </section>

      <div className="mb-3 grid gap-3 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Panel
          name="Actual against predicted, hold-out period"
          hint="Data the model never saw during training"
          actions={
            <Legend
              items={[
                { name: "Actual", color: "#8896ab" },
                { name: "Predicted", color: "#3987e5" },
              ]}
            />
          }
        >
          {backtest.error ? (
            <ErrorState message={backtest.error} onRetry={backtest.reload} />
          ) : backtest.loading && !backtest.data ? (
            <LoadingRows />
          ) : (
            <TimeChart
              data={backtest.data ?? []}
              x="t"
              series={[
                { key: `actual_${target}`, name: "Actual", color: "#8896ab" },
                { key: `predicted_${target}`, name: "Predicted", color: "#3987e5" },
              ]}
              format={dateTime}
              unit={unit}
              height={228}
            />
          )}
        </Panel>
        <Panel name="What drives the forecast" hint="Random forest feature importance for this target">
          {importance.length ? (
            <RankBars
              rows={importance.map(([key, value]) => ({ key, label: featureLabel(key), value: value * 100 }))}
              format={(value) => fixed(value, 1)}
              unit="%"
            />
          ) : (
            <EmptyState message="Available after training" />
          )}
        </Panel>
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
        <Panel name="Model comparison" hint="Measured on the most recent 20% of history, split by time. Models are ranked by RMSE. Data is synthetic, so these figures do not describe real-world accuracy.">
          {metrics.length ? (
            <>
              <DataTable>
                <thead>
                  <tr>
                    <th>Model</th>
                    <th className="!text-right">MAE</th>
                    <th className="!text-right">RMSE</th>
                    <th className="!text-right">R²</th>
                    <th className="!text-right">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {metrics.map((row) => (
                    <tr key={row.model_name}>
                      <td className="font-medium">{row.model_name === "Persistence" ? "Persistence baseline" : row.model_name}</td>
                      <td className="tnum text-right">{fixed(row.mae, 2)}</td>
                      <td className="tnum text-right">{fixed(row.rmse, 2)}</td>
                      <td className="tnum text-right">{fixed(row.r2, 3)}</td>
                      <td className="text-right">{row.selected ? <Pill tone="good">Selected</Pill> : <span className="text-ink-3">–</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </DataTable>
              <p className="tnum mt-2 text-xs text-ink-3">
                Trained {info?.trained_at ? dateTime(info.trained_at) : "–"} on {fixed(info?.train_rows)} rows, tested on{" "}
                {fixed(info?.test_rows)} later rows. Errors are in {unit}. History is simulated, so these measure the
                model on this simulation, not on a real city.
              </p>
            </>
          ) : (
            <EmptyState message={info?.training ? "Training in progress" : "No evaluation yet"} />
          )}
        </Panel>
        <Panel name="Live forecasts against what happened">
          {forecasts.error ? (
            <ErrorState message={forecasts.error} onRetry={forecasts.reload} />
          ) : compared.length ? (
            <DataTable>
              <thead>
                <tr>
                  <th>Target time</th>
                  <th className="!text-right">Predicted</th>
                  <th className="!text-right">Actual</th>
                  <th className="!text-right">Error</th>
                </tr>
              </thead>
              <tbody>
                {compared.slice(-6).map((row) => (
                  <tr key={row.target_at}>
                    <td className="tnum text-ink-2">{clock(row.target_at)}</td>
                    <td className="tnum text-right">{fixed(row.predicted_volume, 1)}</td>
                    <td className="tnum text-right">{fixed(row.actual_volume, 0)}</td>
                    <td className="tnum text-right text-ink-2">
                      {fixed(Math.abs(row.predicted_volume - (row.actual_volume ?? 0)), 1)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          ) : (
            <EmptyState message="Waiting for forecasts to come due" detail="A new forecast is made every 5 simulated minutes and scored once its time arrives" />
          )}
        </Panel>
      </div>
    </>
  );
}
