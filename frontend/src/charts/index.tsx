/** Chart wrappers: thin marks, a scale, a hover layer, recessive axes. */
import type { ReactNode } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState } from "@/components/common";
import { fixed } from "@/utils/format";

const AXIS = { fill: "#8896ab", fontSize: 11 };
const GRID = "#243047";
const TOOLTIP = {
  contentStyle: {
    background: "#111827",
    border: "1px solid #33415c",
    borderRadius: 6,
    fontSize: 12,
    padding: "6px 10px",
  },
  labelStyle: { color: "#b6c2d4", marginBottom: 2 },
  itemStyle: { color: "#f8fafc", padding: 0 },
  cursor: { stroke: "#33415c" },
};

export interface SeriesSpec {
  key: string;
  name: string;
  color: string;
  dashed?: boolean;
}

export function Legend({ items }: { items: { name: string; color: string; dashed?: boolean }[] }) {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-ink-3">
      {items.map((item) => (
        <span key={item.name} className="inline-flex items-center gap-1.5 whitespace-nowrap">
          <span
            className="h-0.5 w-3"
            style={
              item.dashed
                ? { backgroundImage: `linear-gradient(90deg, ${item.color} 60%, transparent 60%)`, backgroundSize: "5px 2px" }
                : { background: item.color }
            }
          />
          {item.name}
        </span>
      ))}
    </div>
  );
}

export function TimeChart<T extends object>({
  data,
  series,
  x,
  height = 180,
  format,
  unit,
  area,
  domain,
  reference,
  animate = false,
}: {
  data: T[];
  series: SeriesSpec[];
  x: keyof T & string;
  height?: number;
  format: (value: string) => string;
  unit?: string;
  area?: boolean;
  domain?: [number | string, number | string];
  reference?: { value: number; label: string };
  animate?: boolean;
}) {
  if (data.length < 2) {
    return <EmptyState message="Collecting readings" detail="The chart fills in as sensors report" />;
  }
  const shared = (
    <>
      <CartesianGrid vertical={false} stroke={GRID} />
      <XAxis
        dataKey={x}
        tick={AXIS}
        tickFormatter={format}
        axisLine={{ stroke: "#33415c" }}
        tickLine={false}
        minTickGap={48}
      />
      <YAxis tick={AXIS} axisLine={false} tickLine={false} width={38} domain={domain ?? [0, "auto"]} />
      <Tooltip
        {...TOOLTIP}
        labelFormatter={(value) => format(String(value))}
        formatter={(value: number, name: string) => [`${fixed(value, value % 1 ? 1 : 0)}${unit ? ` ${unit}` : ""}`, name]}
      />
      {reference ? (
        <ReferenceLine
          y={reference.value}
          stroke="#8896ab"
          strokeDasharray="3 3"
          label={{ value: reference.label, fill: "#8896ab", fontSize: 11, position: "insideTopRight" }}
        />
      ) : null}
    </>
  );
  const margin = { top: 6, right: 8, bottom: 0, left: 0 };
  return (
    <ResponsiveContainer width="100%" height={height}>
      {area ? (
        <AreaChart data={data} margin={margin}>
          {shared}
          {series.map((s) => (
            <Area
              key={s.key}
              dataKey={s.key}
              name={s.name}
              stroke={s.color}
              fill={s.color}
              fillOpacity={0.12}
              strokeWidth={2}
              type="linear"
              isAnimationActive={animate}
              dot={false}
            />
          ))}
        </AreaChart>
      ) : (
        <LineChart data={data} margin={margin}>
          {shared}
          {series.map((s) => (
            <Line
              key={s.key}
              dataKey={s.key}
              name={s.name}
              stroke={s.color}
              strokeWidth={2}
              strokeDasharray={s.dashed ? "4 3" : undefined}
              type="linear"
              isAnimationActive={animate}
              dot={false}
              connectNulls
            />
          ))}
        </LineChart>
      )}
    </ResponsiveContainer>
  );
}

export function Columns<T extends object>({
  data,
  x,
  y,
  name,
  color = "#3987e5",
  height = 180,
  unit,
  highlight,
  format = (value: string) => value,
}: {
  data: T[];
  x: keyof T & string;
  y: keyof T & string;
  name: string;
  color?: string;
  height?: number;
  unit?: string;
  highlight?: (row: T) => boolean;
  format?: (value: string) => string;
}) {
  if (!data.length) return <EmptyState message="No data in this range" />;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: 0 }} barCategoryGap={2}>
        <CartesianGrid vertical={false} stroke={GRID} />
        <XAxis
          dataKey={x}
          tick={AXIS}
          tickFormatter={format}
          axisLine={{ stroke: "#33415c" }}
          tickLine={false}
          minTickGap={4}
        />
        <YAxis tick={AXIS} axisLine={false} tickLine={false} width={44} />
        <Tooltip
          {...TOOLTIP}
          cursor={{ fill: "#1d2840" }}
          labelFormatter={(value) => format(String(value))}
          formatter={(value: number) => [`${fixed(value, value % 1 ? 1 : 0)}${unit ? ` ${unit}` : ""}`, name]}
        />
        <Bar dataKey={y} name={name} fill={color} radius={[3, 3, 0, 0]} isAnimationActive={false}>
          {highlight
            ? data.map((row, index) => <Cell key={index} fill={highlight(row) ? color : "#33415c"} />)
            : null}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Sorted horizontal ranking; the row carries its value, so no axis is needed. */
export function RankBars({
  rows,
  max,
  unit,
  format = (value: number) => fixed(value, 0),
  color = "#3987e5",
}: {
  rows: { key: string; label: ReactNode; value: number | null }[];
  max?: number;
  unit?: string;
  format?: (value: number) => string;
  color?: string;
}) {
  const top = max ?? Math.max(1, ...rows.map((r) => r.value ?? 0));
  return (
    <ul className="space-y-1.5">
      {rows.map((row) => (
        <li key={row.key}>
          <div className="flex items-baseline justify-between gap-3 text-[13px]">
            <span className="truncate text-ink-2">{row.label}</span>
            <span className="tnum whitespace-nowrap">
              {row.value === null ? "–" : format(row.value)}
              {unit && row.value !== null ? <span className="ml-1 text-xs text-ink-3">{unit}</span> : null}
            </span>
          </div>
          <div className="mt-0.5 h-1 rounded-full bg-line">
            <div
              className="h-full rounded-full"
              style={{ width: `${Math.min(100, ((row.value ?? 0) / top) * 100)}%`, background: color }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}
