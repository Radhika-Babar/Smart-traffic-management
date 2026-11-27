import { AnimatePresence, motion } from "framer-motion";

import { Pill } from "@/components/common";
import { Hint } from "@/components/ui/primitives";
import type { ApproachScore, Decision, Direction } from "@/types/api";
import { clock, cn, fixed, title } from "@/utils/format";

function Row({ score, chosen, current }: { score: ApproachScore; chosen: boolean; current: boolean }) {
  return (
    <li className="py-1.5">
      <div className="flex items-center gap-2 text-[13px]">
        <span className={cn("w-12 font-medium", chosen ? "text-ink" : "text-ink-2")}>{title(score.direction)}</span>
        <div className="h-1.5 flex-1 rounded-full bg-line">
          <div
            className="h-full rounded-full transition-[width] duration-300"
            style={{ width: `${score.priority}%`, background: chosen ? "#3b82f6" : "#5b6b86" }}
          />
        </div>
        <span className="tnum w-9 text-right font-medium">{fixed(score.priority, 0)}</span>
      </div>
      <div className="tnum mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 pl-14 text-xs text-ink-3">
        <span>Queue {fixed(score.queue, 0)}</span>
        <span>Density {fixed(score.density, 0)}%</span>
        <span>Wait {fixed(score.waiting, 0)} s</span>
        {current ? <Pill tone="good">Green now</Pill> : null}
        {score.starving ? <Pill tone="bad">Starving</Pill> : null}
        {score.degraded ? (
          <Hint text="Detection sensors are offline, so the controller is estimating this approach from density and time since green.">
            <Pill tone="warn">Estimated</Pill>
          </Hint>
        ) : null}
      </div>
    </li>
  );
}

/** Live priority scores beside the latest decision. Rows keep a fixed order so they can be read while they change. */
export function DecisionExplainer({
  scores,
  decision,
  active,
}: {
  scores: ApproachScore[];
  decision: Decision | null;
  active: Direction | null;
}) {
  return (
    <div>
      <AnimatePresence mode="wait">
        {decision ? (
          <motion.p
            key={decision.decided_at}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.2 }}
            className="mb-1 text-[13px] text-ink"
          >
            {decision.reason}
            <span className="tnum block text-xs text-ink-3">
              {decision.action === "SWITCH" ? "Switched" : "Extended"}
              {decision.direction ? ` ${title(decision.direction)}` : ""} for {fixed(decision.green_s, 0)} s at{" "}
              {clock(decision.decided_at)}
            </span>
          </motion.p>
        ) : (
          <p className="mb-1 text-[13px] text-ink-3">Waiting for the first decision at this intersection</p>
        )}
      </AnimatePresence>
      <ul>
        {scores.map((score) => (
          <Row
            key={score.direction}
            score={score}
            chosen={score.direction === decision?.direction}
            current={score.direction === active}
          />
        ))}
      </ul>
    </div>
  );
}
