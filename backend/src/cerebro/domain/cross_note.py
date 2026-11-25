"""Rules that need other notes, as pure functions over a snapshot ``Mapping[NoteId, NoteRef]``.

The application builds the snapshot from its ports; when validating a change it substitutes the proposed
note's ``NoteRef`` for the stored one (and adds it when new). Rules return findings; severity comes from the
write path's policy.
"""

from collections.abc import Mapping

from cerebro.domain.links import LinkKind
from cerebro.domain.note import NoteId
from cerebro.domain.note_ref import NoteRef
from cerebro.domain.report import Finding, ViolationCode

MAX_HUB_CHAIN_DEPTH = 3
"""An area hub plus at most two levels of sub-area hubs (ADR-0015)."""

Snapshot = Mapping[NoteId, NoteRef]


def cross_note_findings(ref: NoteRef, snapshot: Snapshot) -> tuple[Finding, ...]:
    """Every cross-note rule violation of ``ref`` against ``snapshot``.

    Only problems that involve ``ref`` itself are reported: a cycle through ``ref``, a chain starting at
    ``ref``, its own dangling references. A health report runs this for every note.
    """
    return (
        *broken_link_findings(ref, snapshot),
        *hub_findings(ref, snapshot),
        *superseded_by_cycle_findings(ref, snapshot),
        *hub_chain_findings(ref, snapshot),
    )


def broken_link_findings(ref: NoteRef, snapshot: Snapshot) -> tuple[Finding, ...]:
    """Body links, ``related`` and ``superseded_by`` pointing at an id that does not exist.

    A missing ``hub`` is reported separately by ``hub_findings``. Self references are a per-note rule.
    """
    found: list[Finding] = []
    for link in ref.links:
        if link.kind is LinkKind.HUB or link.target == ref.id or link.target in snapshot:
            continue
        found.append(
            Finding(
                code=ViolationCode.BROKEN_LINK,
                field="body" if link.kind is LinkKind.BODY else link.kind.value,
                message=f"links to {link.target}, which does not exist",
            )
        )
    return tuple(found)


def hub_findings(ref: NoteRef, snapshot: Snapshot) -> tuple[Finding, ...]:
    """``hub`` must reference an existing note of type ``hub``."""
    if ref.hub is None or ref.hub == ref.id:
        return ()
    target = snapshot.get(ref.hub)
    if target is None:
        return (Finding(code=ViolationCode.HUB_NOT_FOUND, field="hub", message=f"hub {ref.hub} does not exist"),)
    if not target.is_hub:
        return (
            Finding(
                code=ViolationCode.HUB_NOT_A_HUB,
                field="hub",
                message=f"{ref.hub} has type {target.type}, not hub",
            ),
        )
    return ()


def superseded_by_cycle_findings(ref: NoteRef, snapshot: Snapshot) -> tuple[Finding, ...]:
    """``superseded_by`` chains that lead back to ``ref`` (a direct self reference is a per-note rule)."""
    if ref.superseded_by is None or ref.superseded_by == ref.id:
        return ()
    path = [ref.id]
    current: NoteId | None = ref.superseded_by
    while current is not None:
        if current == ref.id:
            cycle = " -> ".join(str(i) for i in (*path, ref.id))
            return (Finding(code=ViolationCode.SUPERSEDED_BY_CYCLE, field="superseded_by", message=cycle),)
        if current in path:
            return ()  # a cycle that does not include ref: reported on the notes that are in it
        path.append(current)
        node = snapshot.get(current)
        current = node.superseded_by if node else None
    return ()


def hub_chain_findings(ref: NoteRef, snapshot: Snapshot) -> tuple[Finding, ...]:
    """Hub chains are acyclic and at most ``MAX_HUB_CHAIN_DEPTH`` hubs deep.

    Walks ``hub`` upwards from ``ref``. For a hub the chain includes the hub itself; for any other note it
    is the chain of hubs the note sits under. A broken or non-hub link ends the walk (reported elsewhere).
    """
    if ref.hub is None or ref.hub == ref.id:
        return ()
    chain: list[NoteId] = [ref.id] if ref.is_hub else []
    seen = {ref.id}
    current: NoteId | None = ref.hub
    while current is not None:
        if current in seen:
            names = " -> ".join(str(i) for i in (ref.id, *[i for i in chain if i != ref.id], current))
            return (Finding(code=ViolationCode.HUB_CHAIN_CYCLE, field="hub", message=names),)
        node = snapshot.get(current)
        if node is None or not node.is_hub:
            break
        seen.add(current)
        chain.append(current)
        current = node.hub
    if len(chain) > MAX_HUB_CHAIN_DEPTH:
        names = " -> ".join(str(i) for i in chain)
        return (
            Finding(
                code=ViolationCode.HUB_CHAIN_TOO_DEEP,
                field="hub",
                message=f"{len(chain)} hubs deep (maximum {MAX_HUB_CHAIN_DEPTH}): {names}",
            ),
        )
    return ()
