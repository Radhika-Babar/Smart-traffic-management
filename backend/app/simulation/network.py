"""Road network geometry built from intersection records.

World coordinates are metres with y pointing south, so the map can draw them
directly. An approach named NORTH is the road arriving from the north side of
an intersection. Traffic drives on the right.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

DIRECTION_VECTORS: dict[str, tuple[float, float]] = {
    "NORTH": (0.0, -1.0),
    "SOUTH": (0.0, 1.0),
    "EAST": (1.0, 0.0),
    "WEST": (-1.0, 0.0),
}
OPPOSITE = {"NORTH": "SOUTH", "SOUTH": "NORTH", "EAST": "WEST", "WEST": "EAST"}

GRID_SPACING_X = 420.0
GRID_SPACING_Y = 340.0
ARM_LENGTH = 230.0
BOX_HALF = 14.0
LANE_OFFSET = 5.0
DEFAULT_SPEED_LIMIT = 13.9  # 50 km/h

TURN_WEIGHTS = {"STRAIGHT": 0.62, "RIGHT": 0.2, "LEFT": 0.18}


@dataclass(frozen=True)
class IntersectionSpec:
    id: str
    name: str
    grid_x: int
    grid_y: int
    directions: tuple[str, ...] = ("NORTH", "EAST", "SOUTH", "WEST")
    demand_vph: dict[str, float] = field(default_factory=dict)
    speed_limit: float = DEFAULT_SPEED_LIMIT


@dataclass(eq=False)
class Link:
    id: str
    start: tuple[float, float]
    end: tuple[float, float]
    speed_limit: float
    # Intersection this link flows into, and the approach name there. None when
    # the link leaves the network.
    to_intersection: str | None
    approach: str | None
    from_intersection: str | None
    exit_direction: str | None
    length: float = 0.0
    ux: float = 0.0
    uy: float = 0.0
    turns: list[tuple[Link, float, str]] = field(default_factory=list)
    is_entry: bool = False
    demand_vph: float = 0.0
    # Dynamic state, owned by the engine.
    vehicles: list = field(default_factory=list)
    reserved_m: float = 0.0
    incidents: list = field(default_factory=list)
    backlog: float = 0.0
    crossed_total: int = 0
    queue: int = 0
    avg_wait: float = 0.0
    max_wait: float = 0.0
    speed_sum: float = 0.0
    speed_samples: int = 0
    occupancy_sum: float = 0.0
    occupancy_ticks: int = 0

    def __post_init__(self) -> None:
        dx, dy = self.end[0] - self.start[0], self.end[1] - self.start[1]
        self.length = math.hypot(dx, dy)
        self.ux, self.uy = dx / self.length, dy / self.length

    def point_at(self, pos: float) -> tuple[float, float]:
        return self.start[0] + self.ux * pos, self.start[1] + self.uy * pos

    @property
    def heading(self) -> float:
        return math.atan2(self.uy, self.ux)


@dataclass
class Network:
    intersections: dict[str, IntersectionSpec]
    positions: dict[str, tuple[float, float]]
    links: list[Link]
    # (intersection_id, approach) -> incoming link
    incoming: dict[tuple[str, str], Link]
    # (intersection_id, exit direction) -> outgoing link
    outgoing: dict[tuple[str, str], Link]

    @property
    def entries(self) -> list[Link]:
        return [link for link in self.links if link.is_entry]

    def shortest_path(self, origin: str, destination: str) -> list[str]:
        """Intersections on the shortest route, using road length as the cost."""
        best: dict[str, float] = {origin: 0.0}
        previous: dict[str, str] = {}
        frontier = [(0.0, origin)]
        while frontier:
            cost, node = heapq.heappop(frontier)
            if node == destination:
                break
            if cost > best.get(node, math.inf):
                continue
            for (source, _), link in self.outgoing.items():
                if source != node or link.to_intersection is None:
                    continue
                total = cost + link.length
                if total < best.get(link.to_intersection, math.inf):
                    best[link.to_intersection] = total
                    previous[link.to_intersection] = node
                    heapq.heappush(frontier, (total, link.to_intersection))
        if destination not in best:
            return []
        path = [destination]
        while path[-1] != origin:
            path.append(previous[path[-1]])
        return path[::-1]

    def links_between(self, path: list[str]) -> list[Link]:
        """Links joining consecutive intersections of `path`."""
        result = []
        for here, there in zip(path, path[1:], strict=False):
            result.append(next(
                link for (source, _), link in self.outgoing.items()
                if source == here and link.to_intersection == there
            ))
        return result


def _right_of(ux: float, uy: float) -> tuple[float, float]:
    return -uy, ux


def _lane_points(
    start: tuple[float, float], end: tuple[float, float], trim_start: float, trim_end: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    rx, ry = _right_of(ux, uy)
    return (
        (start[0] + ux * trim_start + rx * LANE_OFFSET, start[1] + uy * trim_start + ry * LANE_OFFSET),
        (end[0] - ux * trim_end + rx * LANE_OFFSET, end[1] - uy * trim_end + ry * LANE_OFFSET),
    )


def _neighbour(spec: IntersectionSpec, direction: str, specs: list[IntersectionSpec]) -> IntersectionSpec | None:
    dx, dy = (int(v) for v in DIRECTION_VECTORS[direction])
    candidates = []
    for other in specs:
        ox, oy = other.grid_x - spec.grid_x, other.grid_y - spec.grid_y
        if dx and oy == 0 and ox * dx > 0:
            candidates.append((abs(ox), other))
        elif dy and ox == 0 and oy * dy > 0:
            candidates.append((abs(oy), other))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _turn_kind(entry_travel: tuple[float, float], exit_travel: tuple[float, float]) -> str | None:
    dot = entry_travel[0] * exit_travel[0] + entry_travel[1] * exit_travel[1]
    if dot > 0.5:
        return "STRAIGHT"
    if dot < -0.5:
        return None  # no U-turns
    cross = entry_travel[0] * exit_travel[1] - entry_travel[1] * exit_travel[0]
    return "RIGHT" if cross > 0 else "LEFT"


def build_network(specs: list[IntersectionSpec]) -> Network:
    positions = {s.id: (s.grid_x * GRID_SPACING_X, s.grid_y * GRID_SPACING_Y) for s in specs}
    links: list[Link] = []
    incoming: dict[tuple[str, str], Link] = {}
    outgoing: dict[tuple[str, str], Link] = {}

    for spec in specs:
        here = positions[spec.id]
        for direction in spec.directions:
            vx, vy = DIRECTION_VECTORS[direction]
            neighbour = _neighbour(spec, direction, specs)
            if neighbour is not None:
                if OPPOSITE[direction] not in neighbour.directions:
                    continue
                there = positions[neighbour.id]
                start, end = _lane_points(here, there, BOX_HALF, BOX_HALF)
                link = Link(
                    f"{spec.id}>{neighbour.id}", start, end, min(spec.speed_limit, neighbour.speed_limit),
                    neighbour.id, OPPOSITE[direction], spec.id, direction,
                )
                links.append(link)
                outgoing[(spec.id, direction)] = link
                incoming[(neighbour.id, OPPOSITE[direction])] = link
            else:
                far = (here[0] + vx * ARM_LENGTH, here[1] + vy * ARM_LENGTH)
                start, end = _lane_points(far, here, 0.0, BOX_HALF)
                entry = Link(f"IN>{spec.id}:{direction}", start, end, spec.speed_limit, spec.id, direction, None, None)
                entry.is_entry = True
                entry.demand_vph = spec.demand_vph.get(direction, 0.0)
                links.append(entry)
                incoming[(spec.id, direction)] = entry
                start, end = _lane_points(here, far, BOX_HALF, 0.0)
                out = Link(f"{spec.id}:{direction}>OUT", start, end, spec.speed_limit, None, None, spec.id, direction)
                links.append(out)
                outgoing[(spec.id, direction)] = out

    for (intersection, _approach), link in incoming.items():
        travel = (link.ux, link.uy)
        for (source, _exit), out in outgoing.items():
            if source != intersection:
                continue
            kind = _turn_kind(travel, (out.ux, out.uy))
            if kind is not None:
                link.turns.append((out, TURN_WEIGHTS[kind], kind))

    return Network({s.id: s for s in specs}, positions, links, incoming, outgoing)
