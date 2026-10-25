"""Hero bodies get their statescript graphs: why the HUD is not empty.

The hero body (003) carries a statescript component whose initial graphs (the bodies table in
data/game_graphs_174.json.gz) are what the client's HUD graph 0x20E4 reads to draw abilities,
weapon and ammo: an instance created by the server does not run its Entry nodes (statescript.py),
so the server sends the schema values (m_1EB5A024) in the owner frame and lets the client-side
nodes do the rest. Without the chunk the body has no graphs and the HUD renders its defaults: no
ability bar, no weapon, the red "can't fire" glyph.

The frames ride the body's create in two steps, as the client's graph manager applies them:
`body_graph_chunk` is the empty graph-info frame at base 1 (it only lifts the manager over the
create tick), `body_full_chunk` is the activating full snapshot at base 0. Listing the instances
in both creates them twice and the client crashes when the activation wait expires; a full frame
wrapped at a numbered base is ignored. A re-pick spawns a new body, so the pair goes again for
the new entity, and the chunk carries the second record's CmFD as any owner frame (a stale one is
dropped, statescript.Stream.next_cmfd).

Two bisection knobs let a deployment narrow the picture without code changes: DROP_GRAPHS leaves
whole graphs out (by 16-bit index), and the `allow` argument restricts which state classes are
activated by name suffix (the SubScript launchers and the HUD gate states are structure and stay
on regardless). With `allow` unset every class is activated except HAS_DATA_STATES below: those
three need the
second-bit has-data form owner_full_frame does not write yet, and a bare active bit for them
shifts every later field in the frame.
"""

from ow174.game import statescript
from ow174.game.script import graph

DROP_GRAPHS: set[int] = set()  # 16-bit graph indexes to leave out of every picture
# The state classes the static picture activates, a set of name suffixes or None for "all the
# one-bit classes the encoder can write". Every body_* function takes it explicitly; SHIP_STATES
# below is what a live server sends. The two-bit classes below ignore it: they move through
# `entries`, because the client must see them begin and stop with real data.
SHIP_STATES = frozenset({"HealthPool", "BooleanSwitch", "Stack", "Wait", "UXPresenter"})
STRUCTURAL = {"STU_37D754C8", "STU_9D7BF987"}  # SubScript launchers and the HUD widget gates

VAR_ID_MASK = 0xFFFFFFFFFFFF  # the .01C identifier's index, without the type bits

# The native predicate over the graph metadata divides the 486 state classes three ways: the many
# one-bit kinds (a bare active bit), a handful whose one-bit entry carries its own state-data bits
# (BooleanSwitch, Stack, the sender class; statescript.DATA_STATES writes them), and three classes
# whose entry is has-data + active + state data (statescript.HAS_DATA_STATES has their builders,
# statescript.HasData is the entry form). The unnamed third class (0x691BFA55) has no builder yet:
# its data reader has not been read, so its states stay unentered - an entry without a mask bit
# consumes nothing, which cannot shift the frame.
HAS_DATA_STATES = {
    "STUStatescriptStateLogicalButton",
    "STUStatescriptStateWeaponVolley",
    "STU_691BFA55",
}


def _allowed(cls: str, allow: set[str] | None) -> bool:
    return allow is None or cls in STRUCTURAL or any(cls == a or cls.endswith(a) for a in allow)


def _value(entry):
    """A schema entry's m_value, the statescript.Value it encodes, None for kinds we skip."""
    kind = entry.get("$")
    value = entry.get("m_value")
    if kind == "STUConfigVarInt" and value is not None:
        return statescript.Int(value)
    if kind == "STUConfigVarBool" and value is not None:
        return statescript.Bool(bool(value))
    if kind == "STUConfigVarFloat" and value is not None:
        return statescript.Float(float(value))
    return None  # null, Texture, ContactSet, ...: the client keeps the graph's default


def _subscript_target(node) -> int | None:
    """The child graph a SubScript (37D754C8) state starts: a 01B GUID in its m_36F7B64C chain, or
    None when the node launches no graph."""
    found: set[int] = set()
    graph._collect_graphs([node.fields], found)
    return next(iter(found), None) if found else None


def body_instances(
    body_guid: int,
    allow: set[str] | None = None,
    entries: dict[tuple[int, int], statescript.HasData] | None = None,
) -> tuple[list, dict]:
    """The body's initial graphs as (instances, entity vars) for an owner full frame.

    Every graph of the body's statescript component becomes a root instance carrying its schema
    values (m_1EB5A024) and every networked state turned on: a server-created instance does not
    run its Entry nodes, so the settled picture a running Entry would reach is sent directly
    (statescript.py). A SubScript launcher is turned on only when the graph it starts is one of
    this body's instances, so the child id it carries is always valid.

    Two entity variables deserve naming here and nowhere else. The first is the hero asset every
    StateHero in the graphs reads (0x03ac): unset, no StateHero ever starts; a null guid there and
    the client exits on activation. The body graphs of the extracted data carry no HUD (0x20E4):
    that graph rides the controller's own frames, and its begin-condition variables belong there."""
    record = graph.bodies().get(body_guid)
    if record is None:
        return [], {}
    by_index = {entry["graph"]: i for i, entry in enumerate(record["graphs"], start=1)
                if entry["graph"] is not None and entry["graph"] not in DROP_GRAPHS}
    instances, entity_vars = [], {}
    from ow174.game.content import heroes  # here: content pulls in the catalogue, keep it lazy

    hero = next((h for h in heroes().values() if h.body == body_guid), None)
    if hero is not None:
        entity_vars.setdefault(0x03AC, statescript.Asset(hero.guid))
    for index, entry in enumerate(record["graphs"], start=1):
        if entry["graph"] is None:
            continue  # the component's array has an empty slot; the client has no instance there
        if entry["graph"] in DROP_GRAPHS:
            continue
        g = graph.graph(entry["graph"])
        if g is None:
            continue  # the body references a graph the extracted data does not carry
        presence = {}
        for schema in entry.get("m_1EB5A024") or []:
            value = _value(schema.get("m_value") or {})
            if value is None:
                continue
            var = int(schema["m_0D09D2D9"], 16) & VAR_ID_MASK
            bit = g.presence_bit(var)
            if bit is not None:
                presence[bit] = value
            else:
                entity_vars[var] = value  # an entity-scope variable: the frame's entity block
        active = {}
        for slot, node in enumerate(g.owner_states()):
            if node.cls == "STU_37D754C8":  # a SubScript launcher: on only for a graph that is an instance
                child = by_index.get(_subscript_target(node))
                if child:
                    active[slot] = statescript.subscript(child)
            elif node.cls in HAS_DATA_STATES:
                # The two-bit classes stay out of the static picture: a volley that starts at
                # spawn fires and never ends (callers move it through `entries` instead), and
                # 0x691BFA55 has no data reader yet. An entry without a mask bit consumes
                # nothing, so leaving them out cannot shift the frame.
                continue
            elif not _allowed(node.cls, allow):
                continue  # outside the allow-list: not activated, no entry in the mask
            elif node.cls in statescript.DATA_STATES:
                active[slot] = statescript.active_data(node.cls)()  # the class's own data bits
            else:
                active[slot] = None  # the one-bit form; the Entry chain a server instance skips
                # would have run these states up (statescript.py)
        for (want, want_slot), value in (entries or {}).items():
            if want == index and want_slot < len(g.owner_states()):
                active[want_slot] = value
        instances.append(
            statescript.Instance(
                index,
                statescript.Graph(entry["graph"], len(g.owner_states()), len(g.presence_vars())),
                presence=presence,
                active=active,
            )
        )
    return instances, entity_vars


def volley_target(body_guid: int) -> tuple[int, int] | None:
    """The body's primary WeaponVolley state as (instance index, owner state slot): the first volley
    in the body's graph order - which is the rifle for Soldier: 76, Ana's and every gun hero whose
    weapons script the state first. The firing frames light exactly this entry."""
    record = graph.bodies().get(body_guid)
    if record is None:
        return None
    for index, entry in enumerate(record["graphs"], start=1):
        if entry["graph"] is None or entry["graph"] in DROP_GRAPHS:
            continue
        g = graph.graph(entry["graph"])
        if g is None:
            continue
        for slot, node in enumerate(g.owner_states()):
            if node.cls == "STUStatescriptStateWeaponVolley":
                return index, slot
    return None


def body_frame(
    body_guid: int,
    cmfd: int,
    lbss: int | None = None,
    allow: set[str] | None = None,
    entries: dict[tuple[int, int], statescript.HasData] | None = None,
):
    """The owner full frame of the body's scripts, None for a body the data does not describe.
    `cmfd` must exceed the last CmFD sent to this entity, as any owner frame does. `entries`
    lights or stops two-bit states on top of the static picture, keyed (instance, state slot)."""
    instances, entity_vars = body_instances(body_guid, allow, entries)
    if not instances:
        return None
    return statescript.owner_full_frame(cmfd, instances, entity_vars, lbss)


def body_graph_chunk(body_guid: int, allow: set[str] | None = None):
    """The first record on a fresh body: an empty graph-info frame at base 1. The fresh entity's
    manager holds no instances until the first full snapshot creates them; listing them here too
    makes the snapshot create them a second time."""
    instances, _ = body_instances(body_guid, allow)
    if not instances:
        return None
    return statescript.chunk(statescript.graph_create_frame([]), 1, 1)


def body_full_chunk(
    body_guid: int,
    cmfd: int,
    allow: set[str] | None = None,
    entries: dict[tuple[int, int], statescript.HasData] | None = None,
):
    """The second record: the activating full snapshot, wrapped at base 0 (last frame 2, so it
    stays above the create's graph-info frame and cannot queue-stall). The same blob wrapped at a
    numbered base is ignored: a full frame at base 0 is what resets and creates the instances."""
    frame = body_frame(body_guid, cmfd, allow=allow, entries=entries)
    return None if frame is None else statescript.chunk(frame, 0, 3)
