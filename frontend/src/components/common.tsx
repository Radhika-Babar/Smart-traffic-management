import { AlertTriangle, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { Button, Hint } from "@/components/ui/primitives";
import type { Aspect, Level } from "@/types/api";
import { cn, LEVEL_COLOR, title } from "@/utils/format";

export function PageHeader({
  name,
  finding,
  actions,
}: {
  name: string;
  finding?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-x-6 gap-y-2">
      <div className="min-w-0">
        <h1 className="text-2xl leading-tight font-semibold">{name}</h1>
        {finding ? <p className="mt-0.5 text-[15px] text-ink-2">{finding}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Panel({
  name,
  hint,
  actions,
  children,
  className,
  flush,
}: {
  name?: ReactNode;
  hint?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  flush?: boolean;
}) {
  return (
    <section className={cn("flex min-w-0 flex-col rounded-lg border border-line bg-card", className)}>
      {name ? (
        <header className="flex min-h-10 items-center justify-between gap-3 px-4 pt-3 pb-1">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold">
            {name}
            {hint ? <Hint text={hint} /> : null}
          </h2>
          {actions ? <div className="flex shrink-0 items-center gap-2 text-xs text-ink-3">{actions}</div> : null}
        </header>
      ) : null}
      <div className={cn("min-h-0 flex-1", !flush && "px-4 pt-1 pb-3")}>{children}</div>
    </section>
  );
}

export function Metric({
  label,
  value,
  unit,
  sub,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  sub?: ReactNode;
  hint?: string;
  tone?: "good" | "warn" | "bad";
}) {
  return (
    <div className="min-w-0 px-4 py-2.5">
      <div className="flex items-center gap-1 text-[13px] text-ink-3">
        <span className="truncate">{label}</span>
        {hint ? <Hint text={hint} /> : null}
      </div>
      <div
        className={cn(
          "tnum mt-0.5 text-2xl leading-tight font-medium",
          tone === "good" && "text-good",
          tone === "warn" && "text-warn",
          tone === "bad" && "text-bad",
        )}
      >
        {value}
        {unit ? <span className="ml-1 text-sm font-normal text-ink-3">{unit}</span> : null}
      </div>
      {sub ? <div className="truncate text-xs text-ink-3">{sub}</div> : null}
    </div>
  );
}

export function LevelBadge({ level }: { level: Level }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 text-xs whitespace-nowrap text-ink-2">
      <span className="size-2 rounded-full" style={{ background: LEVEL_COLOR[level] }} />
      {title(level)}
    </span>
  );
}

const TONE: Record<string, string> = {
  good: "bg-good/15 text-[#86efac]",
  warn: "bg-warn/15 text-[#fcd34d]",
  bad: "bg-bad/15 text-[#fca5a5]",
  info: "bg-accent/15 text-[#93c5fd]",
  neutral: "bg-line text-ink-2",
};

export function Pill({ tone = "neutral", children }: { tone?: keyof typeof TONE; children: ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 flex-none items-center rounded px-1.5 text-xs font-medium whitespace-nowrap",
        TONE[tone],
      )}
    >
      {children}
    </span>
  );
}

const ASPECT_COLOR: Record<Aspect, string> = { RED: "#ef4444", YELLOW: "#f59e0b", GREEN: "#22c55e" };

export function SignalLamp({ aspect, label }: { aspect: Aspect | undefined; label?: string }) {
  return (
    <span
      role="img"
      aria-label={label ?? `${aspect ?? "unknown"} signal`}
      className="inline-block size-2.5 rounded-full"
      style={{ background: aspect ? ASPECT_COLOR[aspect] : "#33415c" }}
    />
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-line/70", className)} />;
}

export function LoadingRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2 py-1" role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className="h-6 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({ message, detail }: { message: string; detail?: string }) {
  return (
    <div className="py-6 text-center">
      <p className="text-[13px] text-ink-2">{message}</p>
      {detail ? <p className="mt-0.5 text-xs text-ink-3">{detail}</p> : null}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-2 py-6 text-center" role="alert">
      <AlertTriangle className="size-4 text-warn" />
      <p className="text-[13px] text-ink-2">{message}</p>
      {onRetry ? (
        <Button size="sm" onClick={onRetry}>
          <RefreshCw /> Retry
        </Button>
      ) : null}
    </div>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block min-w-0">
      <span className="mb-1 flex items-center gap-1 text-xs text-ink-3">
        {label}
        {hint ? <Hint text={hint} /> : null}
      </span>
      {children}
    </label>
  );
}

/** Shared table look: rules and alignment, no bands. */
export function DataTable({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="overflow-x-auto">
      <table
        className={cn(
          "w-full text-[13px] [&_td]:h-10 [&_td]:border-t [&_td]:border-line [&_td]:px-2 [&_td:first-child]:pl-0 [&_td:last-child]:pr-0 [&_th]:h-8 [&_th]:px-2 [&_th]:text-left [&_th]:text-xs [&_th]:font-normal [&_th]:whitespace-nowrap [&_th]:text-ink-3 [&_th:first-child]:pl-0 [&_th:last-child]:pr-0",
          className,
        )}
      >
        {children}
      </table>
    </div>
  );
}
