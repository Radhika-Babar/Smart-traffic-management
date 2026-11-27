import { Maximize, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef } from "react";

import { Button } from "@/components/ui/primitives";
import { frames, useLive } from "@/stores/live";
import { cn } from "@/utils/format";

import { drawMap, fitView, hitTest, offlineByApproach, type MapData, type View } from "./renderer";

export function CityMap({
  selected,
  onSelect,
  className,
  interactive = true,
}: {
  selected?: string | null;
  onSelect?: (id: string | null) => void;
  className?: string;
  interactive?: boolean;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const view = useRef<View | null>(null);
  const size = useRef({ width: 0, height: 0 });
  const topology = useLive((s) => s.topology);
  const live = useLive((s) => s.live);
  const sensors = useLive((s) => s.sensors);

  const intersections = useMemo(
    () => new Map((live?.intersections ?? []).map((item) => [item.id, item])),
    [live],
  );
  const offline = useMemo(() => offlineByApproach(sensors), [sensors]);
  const route = live?.emergencies[0]?.route.slice(live.emergencies[0].route_index) ?? null;

  const data = useRef<MapData | null>(null);
  data.current = topology
    ? { topology, frames, intersections, offline, selected: selected ?? null, emergencyRoute: route, compact: !interactive }
    : null;

  const fit = useCallback(() => {
    if (topology && size.current.width) view.current = fitView(topology, size.current.width, size.current.height);
  }, [topology]);

  useEffect(() => {
    const element = box.current;
    const node = canvas.current;
    if (!element || !node) return;
    const observer = new ResizeObserver(() => {
      const { width, height } = element.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      node.width = Math.round(width * ratio);
      node.height = Math.round(height * ratio);
      size.current = { width, height };
      fit();
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [fit]);

  useEffect(fit, [fit]);

  useEffect(() => {
    const node = canvas.current;
    const context = node?.getContext("2d");
    if (!node || !context) return;
    let handle = 0;
    const render = (now: number) => {
      handle = requestAnimationFrame(render);
      if (!data.current || !view.current) return;
      const ratio = window.devicePixelRatio || 1;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      drawMap(context, size.current.width, size.current.height, view.current, data.current, now);
    };
    handle = requestAnimationFrame(render);
    return () => cancelAnimationFrame(handle);
  }, []);

  const zoom = useCallback((factor: number, px?: number, py?: number) => {
    const current = view.current;
    if (!current) return;
    const x = px ?? size.current.width / 2;
    const y = py ?? size.current.height / 2;
    const scale = Math.min(6, Math.max(0.2, current.scale * factor));
    const applied = scale / current.scale;
    view.current = {
      scale,
      offsetX: x - (x - current.offsetX) * applied,
      offsetY: y - (y - current.offsetY) * applied,
    };
  }, []);

  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);

  return (
    <div ref={box} className={cn("relative overflow-hidden bg-ground", className)}>
      <canvas
        ref={canvas}
        role="img"
        aria-label="Schematic city map with live vehicles and signals. Use the intersection list to select an intersection."
        className={cn("absolute inset-0 size-full", interactive && "cursor-grab touch-none active:cursor-grabbing")}
        onWheel={(event) => {
          if (!interactive) return;
          const bounds = event.currentTarget.getBoundingClientRect();
          zoom(event.deltaY < 0 ? 1.15 : 0.87, event.clientX - bounds.left, event.clientY - bounds.top);
        }}
        onPointerDown={(event) => {
          drag.current = { x: event.clientX, y: event.clientY, moved: false };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          const state = drag.current;
          if (!state || !view.current || !interactive) return;
          const dx = event.clientX - state.x;
          const dy = event.clientY - state.y;
          if (Math.abs(dx) + Math.abs(dy) > 3) state.moved = true;
          if (state.moved) {
            view.current = { ...view.current, offsetX: view.current.offsetX + dx, offsetY: view.current.offsetY + dy };
            state.x = event.clientX;
            state.y = event.clientY;
          }
        }}
        onPointerUp={(event) => {
          const state = drag.current;
          drag.current = null;
          if (!state || state.moved || !topology || !view.current || !onSelect) return;
          const bounds = event.currentTarget.getBoundingClientRect();
          onSelect(hitTest(topology, view.current, event.clientX - bounds.left, event.clientY - bounds.top));
        }}
      />
      {interactive ? (
        <div className="absolute right-3 bottom-3 flex flex-col gap-1">
          <Button size="icon" className="bg-card" aria-label="Zoom in" onClick={() => zoom(1.25)}>
            <Plus />
          </Button>
          <Button size="icon" className="bg-card" aria-label="Zoom out" onClick={() => zoom(0.8)}>
            <Minus />
          </Button>
          <Button size="icon" className="bg-card" aria-label="Fit city to view" onClick={fit}>
            <Maximize />
          </Button>
        </div>
      ) : null}
    </div>
  );
}
