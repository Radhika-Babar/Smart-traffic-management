import { Pill, SignalLamp } from "@/components/common";
import { Button, Hint, Switch } from "@/components/ui/primitives";
import { useAction } from "@/hooks/useFetch";
import { api } from "@/services/api";
import type { Direction, IntersectionLive } from "@/types/api";
import { DIRECTIONS, fixed, title } from "@/utils/format";

export function signalId(intersectionId: string): string {
  return intersectionId.replace("INT", "SIG");
}

export function phaseText(item: IntersectionLive): string {
  if (!item.phase) return "Starting";
  if (item.phase === "ALL_RED" || item.phase === "RED") return "All red clearance";
  return `${title(item.phase)} ${item.active_direction ? title(item.active_direction) : ""}`.trim();
}

/** Mode switch and direction buttons. Commands travel to the field controller over the bus. */
export function SignalControls({ item }: { item: IntersectionLive }) {
  const { run, busy } = useAction();
  const manual = item.mode === "MANUAL";
  const id = signalId(item.id);
  const directions = item.approaches.map((a) => a.direction);

  const request = (direction: Direction) =>
    run(
      () => api.post(`/api/signals/${id}/manual-control`, { direction, green_seconds: 30 }),
      `${title(direction)} requested. It turns green after minimum green and clearance`,
    );

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-[13px] font-medium">Manual control</div>
          <div className="text-xs text-ink-3">
            {manual ? "Operator chooses the direction" : "Adaptive algorithm is in control"}
          </div>
        </div>
        <Switch
          checked={manual}
          label={`Manual control for ${item.name}`}
          disabled={busy}
          onChange={(next) =>
            run(() => api.post(`/api/signals/${id}/mode`, { mode: next ? "MANUAL" : "AUTOMATIC" }))
          }
        />
      </div>
      <div className="grid grid-cols-4 gap-1.5">
        {DIRECTIONS.map((direction) => (
          <Button
            key={direction}
            size="sm"
            disabled={!manual || busy || !directions.includes(direction) || item.active_direction === direction}
            onClick={() => request(direction)}
          >
            <SignalLamp aspect={item.heads[direction]} label={`${direction} is ${item.heads[direction] ?? "off"}`} />
            {title(direction).slice(0, 5)}
          </Button>
        ))}
      </div>
      <p className="tnum text-xs text-ink-3">
        <Hint text="Every switch passes through yellow and all-red. Minimum and maximum green still apply, in manual mode and during emergency priority.">
          <span>Safety sequence always applies</span>
        </Hint>
        {item.phase === "GREEN" ? ` · green for ${fixed(item.elapsed, 0)} s of ${fixed(item.planned_green, 0)} s planned` : ""}
      </p>
      {item.preempt_vehicle ? (
        <Pill tone="bad">
          Priority for {item.preempt_vehicle} from {title(item.preempt_direction ?? "")}
        </Pill>
      ) : null}
    </div>
  );
}
