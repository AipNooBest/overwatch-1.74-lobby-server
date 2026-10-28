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

import json
import re

from ow174.game import statescript
from ow174.game.script import graph

DROP_GRAPHS: set[int] = set()  # 16-bit graph indexes to leave out of every picture
# The state classes the static picture activates, a set of name suffixes or None for "all the
# one-bit classes the encoder can write". Every body_* function takes it explicitly; SHIP_STATES
# below is what a live server sends: the health and HUD structure, the weapon cosmetics (the gun
# model), the logical buttons the firing conditions run through, and the UXPresenter states -
# in the body graphs those are the hero's ability-bar icons, live-confirmed when the list grew
# (the soldier and the zen show their cards; the gun scripts decide theirs by policy, see
# GUN_PRESENTERS). The blanket "all classes" run still dies on spawn: an exotic one-bit state
# reads preloads the server does not send. The animations must be started as transitions (with
# the state already on), like the volley. WeaponVolley moves through `entries` instead -
# the client must see it begin and stop with its data - and the expiry-tick class (0x691BFA55)
# stays out: a live run showed its begin action dies without the type-3 preloads. An entry
# without a mask bit consumes nothing, so a class left out cannot shift the frame.
SHIP_STATES = frozenset({"HealthPool", "BooleanSwitch", "Stack", "Wait",
                         "CosmeticEntity", "LogicalButton", "UXPresenter"})
STRUCTURAL = {"STU_37D754C8", "STU_9D7BF987"}  # SubScript launchers and the HUD widget gates

VAR_ID_MASK = 0xFFFFFFFFFFFF  # the .01C identifier's index, without the type bits

# The native predicate at 0xf5e960 divides the 486 state classes three ways: the many one-bit
# kinds (a bare active bit), a handful whose one-bit entry carries its own state-data bits
# (BooleanSwitch, Stack, the sender class and the launcher; statescript.DATA_STATES writes them),
# and four classes whose entry is has-data + active + state data (statescript.HAS_DATA_STATES has
# their builders, statescript.HasData is the entry form). The predicate's fourth true class,
# 0x2F07046F, has no state in any graph of our data, so its reader can stay unread.
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
    hud_vars: dict[int, statescript.Value] | None = None,
    slots: dict[tuple[int, int], bool] | None = None,
) -> tuple[list, dict]:
    """The body's initial graphs as (instances, entity vars) for an owner full frame.

    Every graph of the body's statescript component becomes a root instance carrying its schema
    values (m_1EB5A024) and every networked state turned on: a server-created instance does not
    run its Entry nodes, so the settled picture a running Entry would reach is sent directly
    (statescript.py). A SubScript launcher is turned on only when the graph it starts is one of
    this body's instances, so the child id it carries is always valid.

    `hud_vars` is the debug chat's rewrite dict (.hudvar): the value of a variable whose id the
    hero HUD graph syncs is written over the shipped presence value, the weapon scripts' synced
    variables take theirs too, and the rest rides the entity block instead (the presenter's
    binding list).

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
        entity_vars.setdefault(HEALTH_VAR, statescript.Float(BODY_HEALTH))
        entity_vars.setdefault(HEALTH_MAX_VAR, statescript.Float(BODY_HEALTH))
        entity_vars.setdefault(ALIVE_VAR, statescript.Bool(True))
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
            elif node.cls == "STUStatescriptStateLogicalButton" and _allowed(node.cls, allow):
                # The firing and HUD conditions run through active buttons, and an active button
                # must carry its press counter: the two-bit entry with zero presses so far.
                active[slot] = statescript.has_data_entry(node.cls)
            elif node.cls == "STU_691BFA55" and allow is not None and _allowed(node.cls, allow):
                # The unnamed expiry-tick class of the has-data family: the entry carries the
                # delay as a sign-magnitude int. A live run activated it and the client died on
                # spawn (the state's begin action reads preloads the server does not send), so
                # only an explicitly named allow-list turns it on; `None` (every one-bit class)
                # leaves it out. The encoder is ready for when the preloads land.
                active[slot] = statescript.has_data_entry(node.cls, ticks=0)
            elif node.cls in HAS_DATA_STATES:
                # A volley lit at spawn fires and never ends - callers move those through
                # `entries` - and an entry without a mask bit consumes nothing, so leaving
                # them out cannot shift the frame.
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
    instances += gun_instances(body_guid, allow, entries, hud_vars, slots)
    if hero is not None:
        # The hero HUD content graphs are resident only once something launches them, and their
        # launchers sit in the dispatcher graph 0x004B the body already owns: the client loads a
        # graph on demand under a launcher whose override names it (this is how 0x120B arrives
        # under the UI host). Light those launchers with the HUD layers as child instances and
        # the readouts draw for the owning player. The health bar and the portrait sit on a
        # sibling layer of the same dispatcher: 0x01CB rides beside 0x01CF, and its presenter
        # needs the same run - the gate class and the UXPresenter state, activated by class so a
        # graph that has neither in it simply ships nothing extra.
        dispatcher = next(
            (i for i in instances if i.graph.index == HERO_HUD_DISPATCHER), None
        )
        dispatch = graph.graph(HERO_HUD_DISPATCHER) if dispatcher is not None else None
        if dispatch is not None:
            launchers: dict[int, int] = {}
            for want, node in enumerate(dispatch.owner_states()):
                if node.cls == "STU_37D754C8":
                    launchers.setdefault(_subscript_target(node), want)
            for hud_graph in HUD_FAMILY:
                hud = graph.graph(hud_graph)
                slot = launchers.get(hud_graph)
                if hud is None or slot is None or slot in dispatcher.active:
                    continue
                hud_index = max(i.index for i in instances) + 1
                presence = {}
                shipped = HERO_HUD_PRESENCE if hud_graph == HERO_HUD_GRAPH else HUD_LAYER_PRESENCE
                if hud_graph == HERO_HUD_GRAPH and hud.presence_bit(HERO_HUD_HERO_VAR) is not None:
                    # the hero asset the portrait widget reads lives in the instance pool
                    shipped = {**shipped, HERO_HUD_HERO_VAR: statescript.Asset(hero.guid)}
                for var, value in shipped.items():
                    bit = hud.presence_bit(var)
                    if bit is not None:
                        presence[bit] = value
                    elif var < 1 << 32:
                        # an entity-scope id of the layer's pool (the gate and the respawn chip
                        # watch those): a presence bit would be read as a different variable,
                        # the entity block is where the layer finds them
                        entity_vars.setdefault(var, value)
                for var, value in (hud_vars or {}).items():
                    bit = hud.presence_bit(var)
                    if bit is not None:
                        presence[bit] = value  # .hudvar: the live rewrite wins
                active = {}
                wanted_buttons: set[int] = set()
                for node in hud.owner_states():
                    if node.cls == "STUStatescriptStateUXPresenter":
                        wanted_buttons.update(
                            int(found)
                            for found in re.findall(
                                r'"m_logicalButton": (\d+)', json.dumps(node.fields)
                            )
                        )
                for want, node in enumerate(hud.owner_states()):
                    if node.cls not in HUD_RUN_STATES:
                        continue
                    if node.cls in statescript.HAS_DATA_STATES:
                        # a button the graph's presenters do not bind would paint its own
                        # widget anyway (0x01CB's button 11 is the top bar line)
                        bound = re.search(r'"m_logicalButton": (\d+)', json.dumps(node.fields))
                        if bound is None or int(bound.group(1)) not in wanted_buttons:
                            continue
                        active[want] = statescript.has_data_entry(node.cls)
                    else:
                        active[want] = None
                instances.append(
                    statescript.Instance(
                        hud_index,
                        statescript.Graph(
                            hud_graph, len(hud.owner_states()), len(hud.presence_vars())
                        ),
                        presence=presence,
                        active=active,
                        parent=(dispatcher.index, slot),
                    )
                )
                dispatcher.active[slot] = statescript.subscript(hud_index)
            entity_vars.setdefault(HERO_HUD_HERO_VAR, statescript.Asset(hero.guid))
            entity_vars.setdefault(0x076C, statescript.Float(0.0))
            for var in HERO_HUD_ENTITY_VARS:
                entity_vars.setdefault(var, statescript.Bool(True))
            hud = graph.graph(HERO_HUD_GRAPH)
            for var, value in (hud_vars or {}).items():
                if hud is not None and hud.presence_bit(var) is None and var < 1 << 32:
                    # the entity-scope bindings and the unbound ids; 48-bit .01B identifiers of
                    # the mount graphs have no wire form here (w_var is 32 bits) - the mount pass
                    # lights them as presence
                    entity_vars[var] = value
    return instances, entity_vars


# The weapon scripts' UXPresenter states light together with the cosmetics: they are the gun's own
# HUD readouts, and the in-game debug chat flips them (.pres) without a server restart, so the
# knob lives here and the encoder reads it on every snapshot. A script that has a presenter reading
# the ammo var gets only those: every gun graph also carries a presenter with no number to present
# (the rifle graph's second weapon card with the infinity sign), and lighting it shows a weapon the
# gun does not have. One bare kind is real: the presenter that reads only the charge var (.003D)
# presents the gun's alternate-fire card - the soldier's Helix Rockets, Lucio's Soundwave -
# and the retail HUD keeps it beside the counter. Scripts whose presenters all read no ammo var - the
# melee and the staff - keep them: there the bare card is the whole readout.
GUN_PRESENTERS = True
# The manager graph's states a live run proved enough to let the weapon cosmetics build (the
# census manager run: manager states 39 and 43 with a cosmetic state turn the gloves into the
# gun). They sit at owner bits 29 and 33 in 1.74's 35-state owner list.
MANAGER_STATES = (29, 33)
# The ammo counter is this sync var of the weapon script (index 53 in its sync-var list, bit 8
# of the presence list); the widget re-reads it whenever the snapshot writes it, so a fresh
# magazine rides the body snapshot itself. AMMO_VAR is the var, AMMO_MAGAZINE the count.
# WEAPON_GATE is the rifle scripts' activity var: while its presence holds a non-zero int the
# script presents its weapon (HUD counter, view model); .0035 and .00C6 are then its numbers.
AMMO_VAR = 0x0035
AMMO_MAGAZINE = 30
# The charge var (.003D): how many rounds the gun's alternate-fire card shows - the helix rockets and
# the like. A presenter whose bindings are only this var is that card, not a spare weapon image.
CHARGE_VAR = 0x003D
WEAPON_GATE_VAR = 0x001C
WEAPON_GATE_VALUE = 50
AMMO_CAP_VAR = 0x00C6
# The hero HUD's readouts are entity variables of the body (the 1.23 in-process reference wrote
# them every tick): the current and maximum health the bar and the numbers show, and the flag
# the readouts hide behind while it says otherwise. A client on a server with no damage takes
# full health, so the snapshot settles the three once and leaves them.
HEALTH_VAR = 0x06C4
HEALTH_MAX_VAR = 0x06C9
ALIVE_VAR = 0x094C
BODY_HEALTH = 250.0  # full health; per-hero tables come when the world starts wounding people

# The hero HUD (abilities bar, ammo and health readouts, ult): the content graph and its
# dispatcher, gate and presenter (the presenter's remote-sync position 13 re-begins it on an
# H=0 frame). The presence values are keyed by the .01C ids of the bound instance pool; the
# entity vars are the rest of the presenter's binding list.
HERO_HUD_DISPATCHER = 0x004B
HERO_HUD_GRAPH = 0x01CF
HERO_HUD_GATE = 40
HERO_HUD_PRESENTER = 28
# The HUD layers the dispatcher launches and the server ships active: 0x01CF carries the ult
# ring readout, its sibling 0x01CB the hero panel layer (96 states, one UXPresenter and one
# gate of the same class). A launcher whose graph is not in this tuple stays unlit.
HUD_FAMILY = (0x01CF,)  # the respawn layer (0x01CB) is out of the shipped family: its group's
# background paints as a top strip unconditionally while the presenter is active, and that is
# the line the target HUD must not have. The chip text itself rides the respawn vars fine (it
# paints with every layer variable zeroed); the right way to bring
# it back is presentation-on-death from the round machine, not a permanently-active presenter.
# The states inside a HUD graph that must run: the presenter that hands the widget set to the
# UI, the gate class the 0x01CF instance ships active (the slot numbers differ per graph,
# so the pair is picked by class), and the logical buttons - a UXPresenter widget bound to a
# logical button only paints while that button is pressed, and entering the button's state is
# what presses it (0x01CB's chip and health widgets bind buttons 20 and 29).
HUD_RUN_STATES = (
    "STUStatescriptStateUXPresenter",
    "STU_9D7BF987",
    # "STUStatescriptStateLogicalButton",  # ABLATION: line still present with these on
)
HERO_HUD_HERO_VAR = 0x0735
HERO_HUD_PRESENCE = {
    0x0A14: statescript.Int(0),
    0x0756: statescript.Int(int(BODY_HEALTH)),  # the current health the bar and its number show
    0x075D: statescript.Int(AMMO_MAGAZINE),
    0x5B85: statescript.Bool(True),
    0x4498: statescript.Int(3),
    0x4499: statescript.Int(0),
    0x2EAB: statescript.Bool(True),
    0x2EAC: statescript.Bool(True),
    0x2EAD: statescript.Bool(True),
    # the rest of the bound pool: the gate watches every bit, so each one gets a value; the
    # charge candidate (.1B84) rides as an int so the ult readout has a percentage to show
    0x070F: statescript.Bool(True),
    0x171B: statescript.Bool(True),
    0x071D: statescript.Bool(True),
    0x0028: statescript.Int(1),  # both HUD gates watch this var; zero keeps the gate closed
    0x2450: statescript.Bool(True),
    0x2451: statescript.Bool(True),
    0x2454: statescript.Bool(True),
    0x0755: statescript.Int(int(BODY_HEALTH)),  # the maximum the health bar and its number read
    0x01A4: statescript.Int(200),
    0x076C: statescript.Int(0),  # the armour part of the bar, empty like a plain hero's
    0x27A3: statescript.Bool(True),
    0x2E4B: statescript.Bool(True),
    0x449A: statescript.Int(0),
    0x6154: statescript.Bool(True),
    0x6180: statescript.Bool(True),
    0x67C7: statescript.Bool(True),
    0x697C: statescript.Bool(True),
    0x227F: statescript.Bool(True),
    0x1B84: statescript.Int(50),
    0x0188: statescript.Bool(True),
    0x01FE: statescript.Bool(True),
}
# The 0x01CB layer's own pool. The layer's gate (owner state 95) watches thirteen of these
# identifiers and stays closed while any of them reads as missing; a live probe filled them
# and the layer lit up (respawn chip, the red health line at the left edge). The health
# numbers ride .065D (current) and .063D (max); .0837 is the float the ChaseVar state lerps
# the ult ring towards.
HUD_LAYER_PRESENCE = {
    0x0028: statescript.Int(200),
    0x0180: statescript.Int(200),
    0x0AFF: statescript.Int(200),
    0x01DE: statescript.Int(200),
    0x0218: statescript.Int(200),
    0x063D: statescript.Int(200),
    0x065D: statescript.Int(200),
    0x0837: statescript.Float(0.0),
    0x0986: statescript.Int(200),
    0x1792: statescript.Int(200),
    0x1794: statescript.Int(200),
    0x22ED: statescript.Int(200),
    0x2CDD: statescript.Int(200),
    0x3523: statescript.Bool(True),
    0x500B: statescript.Int(200),
    0x511B: statescript.Int(200),
    0x54CD: statescript.Int(200),
    0x63C2: statescript.Bool(True),
    0x63C4: statescript.Bool(True),
    0x650B: statescript.Int(0),
    0x65E1: statescript.Int(0),
    0x67DB: statescript.Bool(True),
}
HERO_HUD_ENTITY_VARS = (0x2E27, 0x0757, 0x0406, 0x45C2, 0x2243, 0x2AD4, 0x73ED, 0x001F)

HUD_VAR_USAGE = (
    "Usage: .hudvar <var> <value> - the id in hex, the value in its kind "
    "(e.g. .hudvar 0x1B84 100, .hudvar 0x2EAB off, .hudvar 0x06C4 99.5), "
    "an explicit bool:/int:/float:/asset: prefix when the id is unlisted, clear, or re "
    "(destroy-create the bodies so the rewrites apply: a value lands only as the instance "
    "initializes)."
)


def hud_var_default(var: int) -> statescript.Value | None:
    """The shipped kind of a hero-HUD variable, the value .hudvar's rewrite keeps: a variable the
    shipped tables name carries its kind, anything else must say its kind explicitly."""
    if var in HERO_HUD_PRESENCE:
        return HERO_HUD_PRESENCE[var]
    if var == HERO_HUD_HERO_VAR:
        return statescript.Asset(0)
    if var in (0x076C, HEALTH_VAR, HEALTH_MAX_VAR):
        return statescript.Float(0.0)
    if var == ALIVE_VAR:
        return statescript.Bool(True)
    if var in (WEAPON_GATE_VAR, AMMO_VAR, CHARGE_VAR, 0x00C6):
        return statescript.Int(1)  # the weapon scripts' gate and two counter numbers
    from ow174.game import heroselect  # here: heroselect pulls the screens in, keep it lazy

    if var in heroselect.PANEL_VARS or var in HERO_HUD_ENTITY_VARS:
        return statescript.Bool(True)
    return None


def parse_hud_value(spec: str) -> tuple[int, statescript.Value]:
    """'.hudvar <var> <value>' as (var, value): the id in hex (0x-optional), the value in the
    variable's shipped kind - on/off for the flags, a whole number for the ints, with a decimal
    point for the floats - or any kind explicitly with a bool:/int:/float:/asset: prefix. Unknown ids
    need the explicit kind: the shipped kind is what keeps a frame the length the client knows."""
    var_token, _, value_token = spec.strip().partition(" ")
    value_token = value_token.strip()
    try:
        var = int(var_token, 16)
    except ValueError:
        raise ValueError(f"Bad var id '{var_token}'. " + HUD_VAR_USAGE) from None
    if not value_token:
        raise ValueError("No value. " + HUD_VAR_USAGE)
    default = hud_var_default(var)
    kind = None
    for prefix in ("bool:", "int:", "float:", "asset:"):
        if value_token.lower().startswith(prefix):
            kind, value_token = prefix[:-1], value_token[len(prefix):]
            break
    if kind is None:
        if default is None:
            raise ValueError(
                f"{var:#06x} is in no shipped list; say the kind (bool:/int:/float:/asset:). " + HUD_VAR_USAGE
            )
        kind = {statescript.Bool: "bool", statescript.Int: "int", statescript.Float: "float",
                statescript.Asset: "asset"}[type(default)]
    if kind == "bool":
        if value_token in ("on", "true", "yes"):
            return var, statescript.Bool(True)
        if value_token in ("off", "false", "no"):
            return var, statescript.Bool(False)
        try:
            return var, statescript.Bool(int(value_token, 0) != 0)
        except ValueError:
            raise ValueError(f"{var:#06x} is a flag: on|off. " + HUD_VAR_USAGE) from None
    if kind == "asset":
        try:
            return var, statescript.Asset(int(value_token, 16))
        except ValueError:
            raise ValueError(f"{var:#06x} wants an asset guid in hex. " + HUD_VAR_USAGE) from None
    if "." in value_token:
        if kind == "int":
            raise ValueError(f"{var:#06x} is an int, no decimal point. " + HUD_VAR_USAGE)
        try:
            return var, statescript.Float(float(value_token))
        except ValueError:
            raise ValueError(f"Bad float '{value_token}'. " + HUD_VAR_USAGE) from None
    try:
        number = int(value_token, 0)
    except ValueError:
        raise ValueError(f"Bad number '{value_token}'. " + HUD_VAR_USAGE) from None
    return (var, statescript.Float(float(number))) if kind == "float" else (var, statescript.Int(number))


def _presenter_slots(g) -> tuple[set, set, set]:
    """(every UXPresenter state slot, the slots of presenters whose bindings read the ammo var,
    the slots reading the charge var): the script's own counters and its alternate-fire card. A dynamic
    identifier indexes the graph's synced-var list, but the widget configs use larger ids for
    widget handles, so only in-range ones count as var reads."""
    sv = list(g.sync_vars)

    def reads(fields, want):
        stack = [fields]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("$") == "STUConfigVarDynamic":
                    ident = node.get("m_identifier")
                    if isinstance(ident, int) and ident < len(sv) and sv[ident].var == want:
                        return True
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
        return False

    every = {slot for slot, node in enumerate(g.owner_states())
             if node.cls == "STUStatescriptStateUXPresenter"}
    states = g.owner_states()
    return (every, {slot for slot in every if reads(states[slot].fields, AMMO_VAR)},
            {slot for slot in every if reads(states[slot].fields, CHARGE_VAR)})


def gun_instances(
    body_guid: int,
    allow: set[str] | None,
    entries: dict[tuple[int, int], statescript.HasData],
    hud_vars: dict[int, statescript.Value] | None = None,
    slots: dict[tuple[int, int], bool] | None = None,
) -> list:
    """The manager (0x15) and the weapon scripts' instances for a body: the scripts a gun needs.

    A hero's statescript component names the body graphs (body_instances) plus a manager and up
    to three weapon scripts; the manager starts the cosmetic and animation machinery and a weapon
    script's CosmeticEntity state builds the view model. With only the body graphs the client
    shows bare gloves. The cosmetic state needs no data beyond its active bit (the wire class is
    one bit; the definition it builds must come from the preload list - see content), and the
    weapon script also carries the ammo var, written here so the counter has a number to show.
    `slots` (the debug chat's .hudslot) turns single states on or off on top of the static
    picture, keyed (instance, state slot): off drops the slot from the active set, so the state
    is inactive at init - a presenter switched off this way leaves its HUD slot unrendered.
    """
    record = graph.bodies().get(body_guid)
    if record is None or not _allowed("STUStatescriptStateCosmeticEntity", allow):
        return []
    instances = []
    for index, script in _gun_plan(body_guid)[1]:
        g = graph.graph(script)
        if script == record["manager"]:
            manager_active = {
                slot: None
                for slot in MANAGER_STATES
                if slot < len(g.owner_states())
            }
            for (want, want_slot), value in (entries or {}).items():
                if want == index and want_slot < len(g.owner_states()):
                    manager_active[want_slot] = value
            for (want, want_slot), on in (slots or {}).items():
                if want == index and want_slot < len(g.owner_states()):
                    if on:
                        manager_active[want_slot] = None
                    else:
                        manager_active.pop(want_slot, None)
            instances.append(
                statescript.Instance(
                    index,
                    statescript.Graph(
                        script, len(g.owner_states()), len(g.presence_vars())
                    ),
                    active=manager_active,
                )
            )
            continue
        active = {
            slot: None
            for slot, node in enumerate(g.owner_states())
            if node.cls == "STUStatescriptStateCosmeticEntity"
            or (GUN_PRESENTERS and node.cls == "STUStatescriptStateUXPresenter")
            # The weapon scripts' presenters are the HUD readouts (every hero has its counter
            # slots in the weapon graph; the per-hero tables in docs/HERO-WEAPONS.md list them).
            # Body graphs light their presenters through SHIP_STATES: those are the ability bar.
        }
        if GUN_PRESENTERS:
            every, with_ammo, with_charge = _presenter_slots(g)
            if with_ammo:
                # The gun shows its counter and its ability card; the image-only bare presenters
                # next to a counter would show a weapon the gun does not have.
                for slot in every - with_ammo - with_charge:
                    active.pop(slot, None)
        presence = {}
        bit = g.presence_bit(AMMO_VAR)
        if bit is not None:
            presence[bit] = statescript.Int(AMMO_MAGAZINE)
        gate = g.presence_bit(WEAPON_GATE_VAR)
        if gate is not None:
            presence[gate] = statescript.Int(WEAPON_GATE_VALUE)
        cap = g.presence_bit(AMMO_CAP_VAR)
        if cap is not None:
            presence[cap] = statescript.Int(AMMO_MAGAZINE)
        for var, value in (hud_vars or {}).items():
            wbit = g.presence_bit(var)
            if wbit is not None:
                presence[wbit] = value  # .hudvar reaches the weapon scripts' counters too
        for (want, want_slot), value in (entries or {}).items():
            if want == index and want_slot < len(g.owner_states()):
                active[want_slot] = value
        for (want, want_slot), on in (slots or {}).items():
            if want == index and want_slot < len(g.owner_states()):
                if on:
                    active[want_slot] = None
                else:
                    active.pop(want_slot, None)
        instances.append(
            statescript.Instance(
                index,
                statescript.Graph(script, len(g.owner_states()), len(g.presence_vars())),
                presence=presence,
                active=active,
            )
        )
    return instances


def _gun_plan(body_guid: int) -> tuple[int, list[tuple[int, int]]]:
    """The gun instances as (number of body graphs, [(instance index, weapon graph) ...] including
    the manager first): the indices body_instances hands out, recomputed for callers that need them
    before building the instances."""
    record = graph.bodies().get(body_guid)
    if record is None:
        return 0, []
    plan, index = [], len(record["graphs"]) + 1
    if record["manager"] is not None and graph.graph(record["manager"]) is not None:
        plan.append((index, record["manager"]))
        index += 1
    for weapon in record["weapons"] or []:
        if weapon is not None and graph.graph(weapon) is not None:
            plan.append((index, weapon))
            index += 1
    return len(record["graphs"]), plan


def gun_defs(body_guid: int) -> list[int]:
    """The .003 definition guids the body's weapon cosmetics build - the gun models. The client
    builds a cosmetic only when its definition is resident; the 20505 preload list is where a
    server says so, and these guids are missing from the captured 1.68 list."""
    record = graph.bodies().get(body_guid)
    if record is None:
        return []
    defs = []
    for weapon in record["weapons"] or []:
        g = graph.graph(weapon) if weapon is not None else None
        if g is None:
            continue
        for node in g.owner_states():
            if node.cls != "STUStatescriptStateCosmeticEntity":
                continue
            holder = node.fields.get("m_entityDef") or {}
            guid = holder.get("m_entityDef") if isinstance(holder, dict) else None
            if isinstance(guid, str) and guid.lower().startswith("0x"):
                defs.append(int(guid, 16))
    return defs


def volley_target(body_guid: int) -> tuple[int, int] | None:
    """The gun's WeaponVolley state as (instance index, owner state slot): the first volley in the
    body's weapon scripts - the scripts that also build the view model, and whose volleys drive the
    firing picture - falling back to the body graphs (the order body_instances walks them). The
    firing frames light exactly this entry."""
    record = graph.bodies().get(body_guid)
    if record is None:
        return None
    _, plan = _gun_plan(body_guid)
    for index, weapon in plan:
        if weapon == record["manager"]:
            continue
        g = graph.graph(weapon)
        for slot, node in enumerate(g.owner_states()):
            if node.cls == "STUStatescriptStateWeaponVolley":
                return index, slot
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
    hud_vars: dict[int, statescript.Value] | None = None,
    slots: dict[tuple[int, int], bool] | None = None,
):
    """The owner full frame of the body's scripts, None for a body the data does not describe.
    `cmfd` must exceed the last CmFD sent to this entity, as any owner frame does. `entries`
    lights or stops two-bit states on top of the static picture, keyed (instance, state slot)."""
    instances, entity_vars = body_instances(body_guid, allow, entries, hud_vars, slots)
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
    hud_vars: dict[int, statescript.Value] | None = None,
    slots: dict[tuple[int, int], bool] | None = None,
):
    """The second record: the activating full snapshot, wrapped at base 0 (last frame 2, so it
    stays above the create's graph-info frame and cannot queue-stall). The same blob wrapped at a
    numbered base is ignored: a full frame at base 0 is what resets and creates the instances."""
    frame = body_frame(body_guid, cmfd, allow=allow, entries=entries, hud_vars=hud_vars,
                       slots=slots)
    return None if frame is None else statescript.chunk(frame, 0, 3)
