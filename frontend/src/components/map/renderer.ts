/** Canvas drawing for the schematic city map. No tiles, no network. */
import type { FrameBuffer } from "@/stores/live";
import type { Direction, IntersectionLive, Level, SensorLive, Topology, VehicleRow } from "@/types/api";
import { LEVEL_COLOR } from "@/utils/format";

export interface View {
  scale: number;
  offsetX: number;
  offsetY: number;
}

export interface MapData {
  topology: Topology;
  frames: FrameBuffer;
  intersections: Map<string, IntersectionLive>;
  offline: Map<string, Set<Direction>>;
  selected: string | null;
  emergencyRoute: string[] | null;
  compact: boolean;
}

const ROAD = "#232f4a";
const BLOCK = "#101a2c";
const ROAD_EDGE = "#34446a";
const MARK = "#3d4d6b";
const BOX_HALF = 15;
const LANE = 5;
const LOOKS: Record<string, { color: string; length: number; width: number }> = {
  CAR: { color: "#dbe3ee", length: 6.4, width: 4.6 },
  MOTORCYCLE: { color: "#67e8f9", length: 4, width: 2.6 },
  BUS: { color: "#fbbf24", length: 11.5, width: 5.2 },
  TRUCK: { color: "#c084fc", length: 10, width: 5.2 },
  AMBULANCE: { color: "#ffffff", length: 9, width: 6 },
  FIRE_ENGINE: { color: "#ef4444", length: 12, width: 6.2 },
  POLICE: { color: "#60a5fa", length: 8.5, width: 5.8 },
};
const EMERGENCY = new Set(["AMBULANCE", "FIRE_ENGINE", "POLICE"]);
const ASPECT: Record<string, string> = { RED: "#ef4444", YELLOW: "#f59e0b", GREEN: "#22c55e" };

export const VEHICLE_LEGEND = [
  { label: "Car", color: LOOKS.CAR.color },
  { label: "Motorcycle", color: LOOKS.MOTORCYCLE.color },
  { label: "Bus", color: LOOKS.BUS.color },
  { label: "Truck", color: LOOKS.TRUCK.color },
];

export function fitView(topology: Topology, width: number, height: number): View {
  const xs = topology.links.flatMap((l) => [l.x1, l.x2]);
  const ys = topology.links.flatMap((l) => [l.y1, l.y2]);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const scale = Math.min((width - 8) / (maxX - minX), (height - 8) / (maxY - minY));
  return {
    scale,
    offsetX: (width - (maxX - minX) * scale) / 2 - minX * scale,
    offsetY: (height - (maxY - minY) * scale) / 2 - minY * scale,
  };
}

export function hitTest(topology: Topology, view: View, px: number, py: number): string | null {
  const reach = Math.max(26, 34 * view.scale);
  for (const node of topology.intersections) {
    const dx = node.x * view.scale + view.offsetX - px;
    const dy = node.y * view.scale + view.offsetY - py;
    if (Math.hypot(dx, dy) <= reach) return node.id;
  }
  return null;
}

/** Quiet city blocks between the roads, so the network reads as a place. */
function drawBlocks(ctx: CanvasRenderingContext2D, topology: Topology) {
  const xs = [...new Set(topology.intersections.map((i) => i.x))].sort((a, b) => a - b);
  const ys = [...new Set(topology.intersections.map((i) => i.y))].sort((a, b) => a - b);
  const allX = topology.links.flatMap((l) => [l.x1, l.x2]);
  const allY = topology.links.flatMap((l) => [l.y1, l.y2]);
  const columns = [Math.min(...allX) - 6, ...xs, Math.max(...allX) + 6];
  const rows = [Math.min(...allY) - 6, ...ys, Math.max(...allY) + 6];
  const gap = 20;
  ctx.fillStyle = BLOCK;
  for (let c = 0; c + 1 < columns.length; c++) {
    for (let r = 0; r + 1 < rows.length; r++) {
      const x = columns[c] + (c === 0 ? 0 : gap);
      const y = rows[r] + (r === 0 ? 0 : gap);
      const width = columns[c + 1] - x - (c + 2 === columns.length ? 0 : gap);
      const height = rows[r + 1] - y - (r + 2 === rows.length ? 0 : gap);
      ctx.beginPath();
      ctx.roundRect(x, y, width, height, 8);
      ctx.fill();
    }
  }
}

function drawRoads(ctx: CanvasRenderingContext2D, data: MapData) {
  const { topology, intersections } = data;
  ctx.lineCap = "butt";
  for (const pass of [
    { color: ROAD_EDGE, width: 23 },
    { color: ROAD, width: 21 },
  ]) {
    ctx.strokeStyle = pass.color;
    ctx.lineWidth = pass.width;
    ctx.beginPath();
    for (const link of topology.links) {
      // Each link sits 3.5 m right of the centreline; draw the shared carriageway once.
      const dx = link.x2 - link.x1;
      const dy = link.y2 - link.y1;
      const length = Math.hypot(dx, dy) || 1;
      const nx = (dy / length) * LANE;
      const ny = (-dx / length) * LANE;
      ctx.moveTo(link.x1 + nx, link.y1 + ny);
      ctx.lineTo(link.x2 + nx, link.y2 + ny);
    }
    ctx.stroke();
  }
  // Centre line
  ctx.strokeStyle = MARK;
  ctx.lineWidth = 0.7;
  ctx.setLineDash([7, 7]);
  ctx.beginPath();
  for (const link of topology.links) {
    const dx = link.x2 - link.x1;
    const dy = link.y2 - link.y1;
    const length = Math.hypot(dx, dy) || 1;
    const nx = (dy / length) * LANE;
    const ny = (-dx / length) * LANE;
    ctx.moveTo(link.x1 + nx, link.y1 + ny);
    ctx.lineTo(link.x2 + nx, link.y2 + ny);
  }
  ctx.stroke();
  ctx.setLineDash([]);

  // Congestion on the lane that carries it.
  for (const link of topology.links) {
    if (!link.to_intersection || !link.approach) continue;
    const view = intersections.get(link.to_intersection);
    const approach = view?.approaches.find((a) => a.direction === link.approach);
    if (!approach || approach.level === "LOW") continue;
    ctx.strokeStyle = LEVEL_COLOR[approach.level as Level];
    ctx.globalAlpha = approach.level === "MODERATE" ? 0.28 : 0.5;
    ctx.lineWidth = 9;
    ctx.beginPath();
    const share = Math.min(1, 0.25 + approach.occupancy / 120);
    ctx.moveTo(link.x2 + (link.x1 - link.x2) * share, link.y2 + (link.y1 - link.y2) * share);
    ctx.lineTo(link.x2, link.y2);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
}

function drawRoute(ctx: CanvasRenderingContext2D, data: MapData) {
  if (!data.emergencyRoute || data.emergencyRoute.length < 2) return;
  const nodes = new Map(data.topology.intersections.map((i) => [i.id, i]));
  ctx.strokeStyle = "#ef4444";
  ctx.globalAlpha = 0.45;
  ctx.lineWidth = 4;
  ctx.setLineDash([10, 8]);
  ctx.beginPath();
  data.emergencyRoute.forEach((id, index) => {
    const node = nodes.get(id);
    if (!node) return;
    if (index === 0) ctx.moveTo(node.x, node.y);
    else ctx.lineTo(node.x, node.y);
  });
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
}

function drawJunctions(ctx: CanvasRenderingContext2D, data: MapData) {
  const { topology, intersections, frames, selected, offline } = data;
  const signals = frames.current?.signals ?? {};
  for (const node of topology.intersections) {
    ctx.fillStyle = ROAD;
    ctx.fillRect(node.x - BOX_HALF, node.y - BOX_HALF, BOX_HALF * 2, BOX_HALF * 2);
    const live = intersections.get(node.id);
    const level = live?.congestion ?? "LOW";
    if (level === "HIGH" || level === "SEVERE") {
      ctx.strokeStyle = LEVEL_COLOR[level];
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(node.x, node.y, 34, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (node.id === selected) {
      ctx.strokeStyle = "#3b82f6";
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.arc(node.x, node.y, 40, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  // Stop lines and signal heads at the end of each approach.
  for (const link of topology.links) {
    if (!link.to_intersection || !link.approach) continue;
    const signal = signals[link.to_intersection];
    const aspect = signal?.heads?.[link.approach] ?? "RED";
    const dx = link.x2 - link.x1;
    const dy = link.y2 - link.y1;
    const length = Math.hypot(dx, dy) || 1;
    const ux = dx / length;
    const uy = dy / length;
    const rx = -uy;
    const ry = ux;
    ctx.strokeStyle = ASPECT[aspect];
    ctx.lineWidth = 2.4;
    ctx.beginPath();
    ctx.moveTo(link.x2 - rx * 4.5, link.y2 - ry * 4.5);
    ctx.lineTo(link.x2 + rx * 4.5, link.y2 + ry * 4.5);
    ctx.stroke();
    ctx.fillStyle = ASPECT[aspect];
    ctx.beginPath();
    ctx.arc(link.x2 + rx * 11, link.y2 + ry * 11, 5.2, 0, Math.PI * 2);
    ctx.fill();

    // Sensor site, on the kerb 60 m upstream.
    const sx = link.x2 - ux * 60 + rx * 10;
    const sy = link.y2 - uy * 60 + ry * 10;
    const failed = offline.get(link.to_intersection)?.has(link.approach);
    ctx.fillStyle = failed ? "#ef4444" : "#3b82f6";
    ctx.fillRect(sx - 1.7, sy - 1.7, 3.4, 3.4);
    if (failed) {
      ctx.strokeStyle = "#ef4444";
      ctx.lineWidth = 1;
      ctx.strokeRect(sx - 4, sy - 4, 8, 8);
    }
  }
}

function drawVehicle(ctx: CanvasRenderingContext2D, row: VehicleRow, x: number, y: number, pulse: number) {
  const look = LOOKS[row[4]] ?? LOOKS.CAR;
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(row[3]);
  if (EMERGENCY.has(row[4])) {
    ctx.fillStyle = pulse > 0.5 ? "#ef4444" : "#3b82f6";
    ctx.globalAlpha = 0.28;
    ctx.beginPath();
    ctx.arc(-look.length / 2, 0, 11, 0, Math.PI * 2);
    ctx.fill();
    ctx.globalAlpha = 1;
  }
  // The position is the front bumper.
  ctx.fillStyle = look.color;
  ctx.fillRect(-look.length, -look.width / 2, look.length, look.width);
  if (EMERGENCY.has(row[4])) {
    ctx.fillStyle = pulse > 0.5 ? "#ef4444" : "#60a5fa";
    ctx.fillRect(-look.length * 0.62, -look.width / 2, look.length * 0.24, look.width);
  }
  ctx.restore();
}

function drawVehicles(ctx: CanvasRenderingContext2D, data: MapData, now: number) {
  const { previous, current, receivedAt } = data.frames;
  if (!current) return;
  const blend = Math.min(1, (now - receivedAt) / 200);
  const earlier = new Map<string, VehicleRow>();
  if (previous) for (const row of previous.vehicles) earlier.set(row[0], row);
  const pulse = (now % 500) / 500;
  for (const row of current.vehicles) {
    const before = earlier.get(row[0]);
    let x = row[1];
    let y = row[2];
    if (before && Math.hypot(before[1] - x, before[2] - y) < 60) {
      x = before[1] + (x - before[1]) * blend;
      y = before[2] + (y - before[2]) * blend;
    }
    drawVehicle(ctx, row, x, y, pulse);
  }
}

function drawOverlays(ctx: CanvasRenderingContext2D, data: MapData, view: View) {
  const s = view.scale;
  const toX = (x: number) => x * s + view.offsetX;
  const toY = (y: number) => y * s + view.offsetY;
  const incidents = data.frames.current?.incidents ?? [];
  for (const incident of incidents) {
    const x = toX(incident.x);
    const y = toY(incident.y);
    ctx.fillStyle = "#f59e0b";
    ctx.beginPath();
    ctx.moveTo(x, y - 9);
    ctx.lineTo(x + 9, y + 7);
    ctx.lineTo(x - 9, y + 7);
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = "#0b1220";
    ctx.font = "600 11px Barlow, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("!", x, y + 5);
  }
  ctx.textAlign = "left";
  // On a phone-sized map the chips below it carry the names instead.
  if (data.compact || view.scale < 0.4) return;
  for (const node of data.topology.intersections) {
    const live = data.intersections.get(node.id);
    const x = toX(node.x) + 28 * s + 10;
    const y = toY(node.y) - 28 * s - 8;
    ctx.font = "500 12px Barlow, sans-serif";
    const name = node.name;
    const queue = live ? live.approaches.reduce((sum, a) => sum + a.queue, 0) : 0;
    const detail = `${node.id} · queue ${Math.round(queue)}`;
    const width = Math.max(ctx.measureText(name).width, ctx.measureText(detail).width) + 12;
    ctx.fillStyle = "rgba(11,18,32,0.82)";
    ctx.fillRect(x - 6, y - 13, width, 33);
    ctx.fillStyle = "#f8fafc";
    ctx.fillText(name, x, y);
    ctx.fillStyle = "#8896ab";
    ctx.font = "400 11px Barlow, sans-serif";
    ctx.fillText(detail, x, y + 14);
  }
}

export function drawMap(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  view: View,
  data: MapData,
  now: number,
) {
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#0b1220";
  ctx.fillRect(0, 0, width, height);
  ctx.save();
  ctx.translate(view.offsetX, view.offsetY);
  ctx.scale(view.scale, view.scale);
  drawBlocks(ctx, data.topology);
  drawRoads(ctx, data);
  drawRoute(ctx, data);
  drawJunctions(ctx, data);
  drawVehicles(ctx, data, now);
  ctx.restore();
  drawOverlays(ctx, data, view);
}

export function offlineByApproach(sensors: SensorLive[]): Map<string, Set<Direction>> {
  const result = new Map<string, Set<Direction>>();
  for (const sensor of sensors) {
    if (sensor.status !== "OFFLINE" || !sensor.direction) continue;
    if (!result.has(sensor.intersection_id)) result.set(sensor.intersection_id, new Set());
    result.get(sensor.intersection_id)!.add(sensor.direction);
  }
  return result;
}
