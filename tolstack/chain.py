"""Find mating faces between parts and build the 1-D stack-up chain between two faces."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from .cad import Part, PlanarFace
from .stack import Contributor


@dataclass
class Contact:
    """Bottom face of `upper` sits on top face of `lower` at `level`."""
    lower: int
    upper: int
    level: float
    overlap: float
    lower_face: PlanarFace
    upper_face: PlanarFace


def _overlap(r1, r2) -> float:
    du = min(r1[2], r2[2]) - max(r1[0], r2[0])
    dv = min(r1[3], r2[3]) - max(r1[1], r2[1])
    return du * dv if du > 0 and dv > 0 else 0.0


def find_contacts(faces: list[PlanarFace], tol: float = 0.02, min_overlap: float = 0.05) -> list[Contact]:
    """Pairs of opposite-facing planar faces of different parts at the same level that overlap."""
    buckets = defaultdict(list)
    for face in faces:
        buckets[round(face.level / tol)].append(face)
    best: dict[tuple, Contact] = {}
    for key, bucket in buckets.items():
        candidates = bucket + buckets.get(key - 1, []) + buckets.get(key + 1, [])
        tops = [f for f in bucket if f.sign > 0]
        bottoms = [f for f in candidates if f.sign < 0]
        for top in tops:
            for bottom in bottoms:
                if top.part == bottom.part or abs(top.level - bottom.level) > tol:
                    continue
                area = _overlap(top.rect, bottom.rect)
                if area < min_overlap:
                    continue
                pair = (top.part, bottom.part)
                if pair not in best or area > best[pair].overlap:
                    best[pair] = Contact(top.part, bottom.part, top.level, area, top, bottom)
    return list(best.values())


def find_path(start_part: int, end_part: int, contacts: list[Contact], exclude: set[int] = frozenset()):
    """Shortest chain of parts linked by contacts (breadth-first)."""
    graph = defaultdict(list)
    for c in contacts:
        if c.lower in exclude or c.upper in exclude:
            continue
        graph[c.lower].append((c.upper, c))
        graph[c.upper].append((c.lower, c))
    previous = {start_part: None}
    queue = deque([start_part])
    while queue:
        node = queue.popleft()
        if node == end_part:
            break
        for neighbour, contact in graph[node]:
            if neighbour not in previous:
                previous[neighbour] = (node, contact)
                queue.append(neighbour)
    if end_part not in previous:
        return None
    steps = []
    node = end_part
    while previous[node] is not None:
        parent, contact = previous[node]
        steps.append((parent, node, contact))
        node = parent
    return list(reversed(steps))


def build_chain(start: PlanarFace, end: PlanarFace, parts: list[Part], contacts: list[Contact],
                exclude: set[int] = frozenset()) -> list[Contributor]:
    """Loop from the start face through the parts to the end face.

    Each part contributes the distance between the two faces it uses (entry and exit).
    direction = +1 when the loop moves towards +axis. The signed sum equals end.level - start.level.
    """
    if start.part == end.part:
        steps = []
    else:
        steps = find_path(start.part, end.part, contacts, set(exclude) - {start.part, end.part})
        if steps is None:
            raise ValueError("no contact path between the two parts; check the axis or excluded parts")
    levels = [(start.part, start.level)]
    for parent, child, contact in steps:
        levels.append((parent, contact.level))
        levels.append((child, contact.level))
    levels.append((end.part, end.level))
    contributors = []
    for i in range(0, len(levels), 2):
        part_index, entry = levels[i]
        _, exit_level = levels[i + 1]
        delta = exit_level - entry
        if abs(delta) < 1e-6:
            continue
        name = parts[part_index].name
        contributors.append(Contributor(
            label="",
            part=name,
            description=f"{name}: face @ {entry:.3f} -> face @ {exit_level:.3f}",
            nominal=round(abs(delta), 4),
            tol_plus=0.0,
            tol_minus=0.0,
            direction=1 if delta > 0 else -1,
            source="3D",
        ))
    for i, contributor in enumerate(contributors):
        contributor.label = _letter(i)
    return contributors


def _letter(i: int) -> str:
    letters = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        letters = chr(65 + r) + letters
    return letters
