"""Find mating faces between parts and build the 1-D stack-up chain between two faces.

A *contact* is a top face of one part and a bottom face of another part at the same level
(within `tol`) whose true outlines overlap. Between two faces there can be several chains
through the assembly; `find_paths` returns them ranked so the user can see and choose the
load path instead of trusting one silent answer.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from .cad import Part, PlanarFace, face_outline
from .stack import Contributor


@dataclass
class Contact:
    """Bottom face of `upper` sits on top face of `lower` at `level`."""
    lower: int
    upper: int
    level: float
    overlap: float          # true overlap area of the two face outlines (mm^2)
    lower_face: PlanarFace
    upper_face: PlanarFace
    gap: float = 0.0        # level difference between the two faces (<= contact tolerance)


@dataclass
class ChainOption:
    """One candidate chain of parts between the start and end faces."""
    steps: list             # [(from_part, to_part, Contact), ...]
    hops: int
    min_overlap: float      # weakest contact on the path (mm^2)

    def describe(self, parts: list[Part]) -> str:
        if not self.steps:
            return "same part (no contacts needed)"
        text = parts[self.steps[0][0]].name
        for _, child, contact in self.steps:
            text += f" -> {parts[child].name}"
        levels = ", ".join(f"{c.level:.3f}" for _, _, c in self.steps)
        return (f"{text}   ({self.hops} contacts at {levels}; weakest overlap {self.min_overlap:.1f} mm2)")


def _rect_overlap(r1, r2) -> float:
    du = min(r1[2], r2[2]) - max(r1[0], r2[0])
    dv = min(r1[3], r2[3]) - max(r1[1], r2[1])
    return du * dv if du > 0 and dv > 0 else 0.0


def _true_overlap(top: PlanarFace, bottom: PlanarFace) -> float | None:
    a, b = face_outline(top), face_outline(bottom)
    if a is None or b is None:
        return None
    return a.intersection(b).area


def find_contacts(faces: list[PlanarFace], tol: float = 0.02, min_overlap: float = 0.05,
                  exact: bool = True) -> list[Contact]:
    """All pairs of opposite-facing planar faces of different parts at the same level that overlap.

    The bounding-box overlap is a fast pre-filter; with `exact=True` the real face outlines
    (holes and L-shapes included) are intersected, so two faces whose boxes overlap but whose
    material does not touch are not reported as a contact.
    """
    buckets = defaultdict(list)
    for face in faces:
        buckets[round(face.level / tol)].append(face)
    contacts: list[Contact] = []
    seen = set()
    for key, bucket in buckets.items():
        candidates = bucket + buckets.get(key - 1, []) + buckets.get(key + 1, [])
        tops = [f for f in bucket if f.sign > 0]
        bottoms = [f for f in candidates if f.sign < 0]
        for top in tops:
            for bottom in bottoms:
                if top.part == bottom.part or abs(top.level - bottom.level) > tol:
                    continue
                ident = (top.part, top.face_id, bottom.part, bottom.face_id)
                if ident in seen:
                    continue
                seen.add(ident)
                area = _rect_overlap(top.rect, bottom.rect)
                if area < min_overlap:
                    continue
                if exact:
                    true_area = _true_overlap(top, bottom)
                    if true_area is not None:
                        area = true_area
                if area < min_overlap:
                    continue
                contacts.append(Contact(top.part, bottom.part, top.level, area, top, bottom,
                                        gap=round(bottom.level - top.level, 6)))
    return contacts


def _graph(contacts: list[Contact], exclude: set[int]):
    """Multigraph: every distinct contact level between two parts is its own edge.

    Two parts can touch at more than one level (a stepped seat, a rib and a flange). Each level
    gives a different chain, so they are kept apart and ranked instead of silently keeping the
    largest one. Duplicate face pairs at the same level are merged (largest overlap kept).
    """
    best: dict[tuple, Contact] = {}
    for c in contacts:
        if c.lower in exclude or c.upper in exclude:
            continue
        key = (min(c.lower, c.upper), max(c.lower, c.upper), c.lower, round(c.level, 3))
        if key not in best or c.overlap > best[key].overlap:
            best[key] = c
    graph = defaultdict(list)
    for c in best.values():
        graph[c.lower].append((c.upper, c))
        graph[c.upper].append((c.lower, c))
    for node in graph:
        graph[node].sort(key=lambda item: -item[1].overlap)
    return graph


def find_path(start_part: int, end_part: int, contacts: list[Contact], exclude: set[int] = frozenset()):
    """Shortest chain of parts linked by contacts (breadth-first). None if there is no path."""
    options = find_paths(start_part, end_part, contacts, exclude, max_paths=1)
    return options[0].steps if options else None


def find_paths(start_part: int, end_part: int, contacts: list[Contact], exclude: set[int] = frozenset(),
               max_paths: int = 5, extra_hops: int = 2, budget: int = 50_000) -> list[ChainOption]:
    """Candidate chains, ranked: fewest contacts first, then the strongest weakest contact.

    Paths up to `extra_hops` longer than the shortest one are listed (bounded search, so it
    stays fast on assemblies with hundreds of parts).
    """
    if start_part == end_part:
        return [ChainOption([], 0, float("inf"))]
    graph = _graph(contacts, set(exclude) - {start_part, end_part})
    depth = {start_part: 0}
    queue = deque([start_part])
    while queue:
        node = queue.popleft()
        for neighbour, _ in graph[node]:
            if neighbour not in depth:
                depth[neighbour] = depth[node] + 1
                queue.append(neighbour)
    if end_part not in depth:
        return []
    limit = depth[end_part] + extra_hops
    # distance to the end, for pruning
    to_end = {end_part: 0}
    queue = deque([end_part])
    while queue:
        node = queue.popleft()
        for neighbour, _ in graph[node]:
            if neighbour not in to_end:
                to_end[neighbour] = to_end[node] + 1
                queue.append(neighbour)

    found: list[ChainOption] = []
    expansions = 0
    stack = [(start_part, [], {start_part})]
    while stack and expansions < budget:
        node, steps, visited = stack.pop()
        expansions += 1
        if node == end_part:
            found.append(ChainOption(steps, len(steps), min(c.overlap for _, _, c in steps)))
            continue
        for neighbour, contact in reversed(graph[node]):
            if neighbour in visited or neighbour not in to_end:
                continue
            if len(steps) + 1 + to_end[neighbour] > limit:
                continue
            stack.append((neighbour, steps + [(node, neighbour, contact)], visited | {neighbour}))
    found.sort(key=lambda o: (o.hops, -o.min_overlap))
    return found[:max_paths]


def build_chain(start: PlanarFace, end: PlanarFace, parts: list[Part], contacts: list[Contact],
                exclude: set[int] = frozenset(), steps: list | None = None) -> list[Contributor]:
    """Loop from the start face through the parts to the end face.

    Each part contributes the distance between the two faces it uses (entry and exit).
    direction = +1 when the loop moves towards +axis. The signed sum equals end.level - start.level
    (checked; a mismatch raises, so a broken chain can never reach the report).
    `steps` selects one of the options from `find_paths`; default = the best ranked option.
    """
    if steps is None:
        if start.part == end.part:
            steps = []
        else:
            steps = find_path(start.part, end.part, contacts, set(exclude) - {start.part, end.part})
            if steps is None:
                raise ValueError(
                    f"no contact path between {parts[start.part].name} and {parts[end.part].name}. "
                    "Check the stack-up axis, raise the contact tolerance, or remove parts from the "
                    "exclude list. Parts held only by screws or snaps have no face contact: add them "
                    "as manual rows.")
    levels = [(start.part, start.level)]
    for parent, child, contact in steps:
        levels.append((parent, contact.lower_face.level if contact.lower == parent else contact.upper_face.level))
        levels.append((child, contact.lower_face.level if contact.lower == child else contact.upper_face.level))
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
    for parent, child, contact in steps:
        if abs(contact.gap) > 1e-6:
            # gap = upper face level - lower face level; travelling downwards reverses its sign
            delta = contact.gap if parent == contact.lower else -contact.gap
            contributors.append(Contributor(
                label="", part=f"{parts[contact.lower].name}/{parts[contact.upper].name}",
                description=f"modelled gap at contact @ {contact.level:.3f}",
                nominal=round(abs(delta), 4), tol_plus=0.0, tol_minus=0.0,
                direction=1 if delta > 0 else -1, source="3D contact gap"))
    for i, contributor in enumerate(contributors):
        contributor.label = _letter(i)
    closure = sum(c.direction * c.nominal for c in contributors)
    if abs(closure - (end.level - start.level)) > 1e-3:
        raise ValueError(f"chain does not close: sum {closure:.4f} != 3D {end.level - start.level:.4f}")
    return contributors


def _letter(i: int) -> str:
    letters = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        letters = chr(65 + r) + letters
    return letters
