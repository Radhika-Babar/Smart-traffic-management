import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

import type { Direction, Level } from "@/types/api";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export const DIRECTIONS: Direction[] = ["NORTH", "EAST", "SOUTH", "WEST"];

export const SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9"];

/** Colour belongs to the direction, so a filter never repaints a surviving series. */
export const DIRECTION_COLOR: Record<Direction, string> = {
  NORTH: SERIES[0],
  EAST: SERIES[1],
  SOUTH: SERIES[2],
  WEST: SERIES[3],
};

export const LEVEL_COLOR: Record<Level, string> = {
  LOW: "#22c55e",
  MODERATE: "#eab308",
  HIGH: "#f59e0b",
  SEVERE: "#ef4444",
};

export function title(value: string): string {
  const text = value.replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function fixed(value: number | null | undefined, digits = 0, fallback = "–"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback;
  return value.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function kmh(metresPerSecond: number | null | undefined): string {
  return metresPerSecond === null || metresPerSecond === undefined ? "–" : fixed(metresPerSecond * 3.6, 0);
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return "–";
  return new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function shortTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  const moment = new Date(iso);
  return `${moment.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })} ${moment.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "–";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} m ${Math.round(seconds % 60)} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} m`;
}

export function secondsBetween(from: string | null, to: string | null): number | null {
  if (!from || !to) return null;
  return (new Date(to).getTime() - new Date(from).getTime()) / 1000;
}
