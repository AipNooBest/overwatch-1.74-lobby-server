"""The controller's statescript on a player's entity: the HUD and the hero select screen.

Each team of a game mode names its controller graph. The Practice Range's is 13C1: its SubScript
states start the HUD (state 42 -> graph 20E4) and hero select (state 43 -> 288A, whose state 0 ->
288B). The PvP modes use 0C90, whose state 119 starts 288A; its HUD graph is client-only, so the
client makes that one itself. 288B presents the screen 008C.05A ("CHOOSE YOUR HERO") to the local player
while two entity variables are on: v2438 (hero select is open, the server's) and v31435 (what 288B's
client-only nodes read). The client sends the pick as ClientOutInstance 21616 {entity, parameters,
game message 1361.025, instance}, with the hero among the parameters; 1360.025 asks to open or close
the screen. Retail OW2 traffic works the same way.

More of the screen (read in the graphs and in IDA):
- The skin selector shows while 288B's v25377 ("assembling heroes") is on. In retail the PvP mode
  script sets it for the assemble phase, and we do the same; in the Practice Range we set it whenever
  the screen is open, and hide the assemble title that comes with it (v8535). With it on, the client
  leaves the screen open after a pick, so the server closes it. A chosen skin goes to the lobby
  (24500), and the body wears it (world.create).
- The team list: 288A's state 1 starts 288D, whose client-only nodes post their entity (game message
  134F.025) to the local player's 288B unless it is an enemy. On our own entity that puts us in the list.
"""

from dataclasses import dataclass

from ow174.game.content import PRACTICE_RANGE_MODE, heroes
from ow174.game.statescript import (
    CONTROLLER,
    HERO_SELECT,
    HERO_SELECT_HOST,
    HUD,
    PVP_CONTROLLER,
    TEAM_ENTRY,
    Bool,
    Float,
    Graph,
    Instance,
    Int,
    chunk,
    graph_create_frame,
    owner_full_frame,
    remote_frame,
    stack_data,
    subscript,
    switch_data,
)

OPEN_SELECT = 2438
SELECT_SHOWN = 31435
ASSEMBLING = 0  # 288B's presence bit of v25377
# 288B's v8535 feeds the screen's IsAssemblingHeroesHidden: on, "ASSEMBLE YOUR TEAM" and its timer go. Only
# a client-only Stack writes it (on 06A7.025, which some mode graphs post), so a value from the server
# stays.
HIDE_ASSEMBLE = 8535
PICK_HERO = 0x0240000000001361  # 1361.025 {hero}
OPEN_OR_CLOSE = 0x0240000000001360  # 1360.025 {open}
HERO_TYPE = 0x02E0  # the type bits of a 075 hero GUID


@dataclass(frozen=True)
class Controller:
    graph: Graph
    allow_bit: int  # the presence bit of v8419, m_allowHeroSelect
    hero_select_bit: int  # the HeroSelect state
    select_bit: int  # the SubScript state that starts 288A: its state bit, and its state index
    select_state: int
    hud_bit: int | None = None  # the SubScript state that starts 20E4, if the server has to
    hud_state: int | None = None


PRACTICE = Controller(CONTROLLER, 5, 25, 37, 43, 36, 42)
PVP = Controller(PVP_CONTROLLER, 19, 1, 103, 119)


def controller_of(mode_guid: int) -> Controller:
    return PRACTICE if mode_guid == PRACTICE_RANGE_MODE else PVP


# 20E4's one owner state and the two entity vars its subgraph begins on. A live client proved the
# shape: mount the HUD and set these together with the body's scripts; mounting it without the
# state on leaves the subgraph empty, and the hero select grid loses its heroes.
HUD_GATE = 0
HUD_BEGIN_VARS = (17906, 18405)
# The UI side's own variables, as entity variables of the same snapshot: the HUD layers exist only
# after UI graph 041A's state 20 turns its mode machine on. That state's condition is an OR of
# five terms, one of which is the global var 0x09BA (ui-graphs-casc report B3/B4), and the
# dispatcher graph 046B keeps the preload-sync var 0x401F. The retail server replicates them over
# the statescript component's global sync-var section - the frame's entity-variable list is that
# section (id-delta entries terminated by id 0, bools one bit), so they ride the UI entity's
# snapshot next to the begin vars. 09FC (preload, the 041A pool) comes along: an entity variable
# the client has no slot for is stored and unused, not an error (the hero select screen has
# ignored unknown vars in its own snapshots since the first live runs).
UI_HUD_VARS = (0x09BA, 0x401F, 0x09FC)


def controller_frame(
    controller: Controller,
    cmfd: int,
    select_open: bool,
    skins: bool = False,
    hide_assemble: bool = False,
    hud: bool = False,
    overrides: dict | None = None,
):
    """The owner full frame for the player's entity: the controller (1) with hero select 288A (2) ->
    288B (3) under it, 13C1's HUD 20E4 (4), and 288A's team list entry 288D (the next index).
    `skins` turns on v25377, which shows the skin selector; `hide_assemble` hides the assemble title
    and timer that come with it. `hud` mounts the HUD subgraph on (the gate state and its begin
    variables) rather than just creating the instance: pass it when the hero's body scripts ride the
    body, the configuration the HUD widgets read their data from. `overrides` is the debug chat's
    .hudvar rewrite: a synced variable of the controller graph joins the controller instance's
    presence, anything else rides the entity block."""
    active = {controller.hero_select_bit: None, controller.select_bit: subscript(2)}
    host = Instance(2, HERO_SELECT_HOST, parent=(1, controller.select_state), active={0: subscript(3)})
    screen = Instance(3, HERO_SELECT, parent=(2, 0))
    instances = [
        Instance(1, controller.graph, presence={controller.allow_bit: Bool(True)}, active=active),
        host,
        screen,
    ]
    variables = {OPEN_SELECT: Bool(select_open), SELECT_SHOWN: Bool(select_open)}
    if controller.hud_bit is not None:
        active[controller.hud_bit] = subscript(4)
        states = {HUD_GATE: None} if hud else {}
        if hud:
            variables.update(dict.fromkeys(HUD_BEGIN_VARS + UI_HUD_VARS, Bool(True)))
        instances.append(Instance(4, HUD, parent=(1, controller.hud_state), active=states))
    entry = len(instances) + 1
    host.active[1] = subscript(entry)
    instances.append(Instance(entry, TEAM_ENTRY, parent=(2, 1)))
    if skins:
        screen.presence[ASSEMBLING] = Bool(True)
    if hide_assemble:
        screen.extra[HIDE_ASSEMBLE] = Bool(True)
    if overrides:
        from ow174.game.script import graph as graph_data  # here: keep the parser import lazy

        synced = graph_data.graph(controller.graph.index)
        for var, value in overrides.items():
            bit = synced.presence_bit(var) if synced is not None else None
            if bit is not None:
                instances[0].presence[bit] = value  # the controller instance's own pool
            else:
                variables[var] = value  # entity scope: the begin-condition block
    return owner_full_frame(cmfd, instances, variables)


# The native layer machine: its state 15 (the 15-way STU_501733D7) is the one thing in the client
# that creates HUD_Root and the HUD layers, and every entry node of the graph is networked - so a
# client-created instance never runs them and only an owner-frame active bit lights the state. On
# the client's own UI entity the machine is driven by the UI manager's mode byte; mounted on the
# server's world-owned UI entity it runs from the wire, under the 041A root of the retail boot
# chain (the layer gate is a five-term OR; on this host its terms are the snapshot's entity
# variables and the world ownership itself - worknotes/ui-mount-hud-root-2026-10-26.md).
UI_HOST = Graph(0x041A, 33, 7, 11)
UI_HOST_GATE = 20  # the BooleanSwitch the chain begins on
UI_HOST_LAUNCH = 25  # the SubScript state that starts 0x120B
UI_MACHINE = Graph(0x120B, 44, 8, 23)
MACHINE_ROOT = 15
MACHINE_MESSAGE = 22  # the remote-sync position of the .0C47 listener (worknotes S2)
MACHINE_PANEL = 22  # the SubScript state whose GWO is the hero panel graph
# The ammo widget (worknotes/hud-msg-indices S3): three UXPresenters, a branch switch and a
# .0C47 listener; the number is its own instance var 0x0035, presence bit 2, and the widget
# re-reads it whenever a snapshot writes it. The hero panel (0x183C) has one networked state,
# the gate; its nine entity variables are read by the client-side preload and stay at defaults.
AMMO_WIDGET = Graph(0x20E7, 5, 5, 4)
HERO_PANEL = Graph(0x183C, 1, 0, 1)
PANEL_VARS = (0x0520, 0x4580, 0x2F47, 0x57A8, 0x2C2A, 0x43CE, 0x2613, 0x099B, 0x1A3C)


def world_ui_graph_chunk():
    """The UI entity's first record: the empty graph-info chunk at base 1, mirroring the bodies'
    two-step sequence (§ the create must not list the instances the snapshot creates)."""
    return chunk(
        graph_create_frame(
            [
                Instance(1, UI_HOST),
                Instance(2, UI_MACHINE),
                Instance(3, HUD),
                Instance(4, AMMO_WIDGET),
                Instance(5, HERO_PANEL),
            ]
        ),
        1,
        1,
    )


def world_ui_chunk(cmfd: int, overrides: dict | None = None):
    """The HUD's own mount on a server-created UI entity: the widget's visibility predicate
    resolves through the UI instance's owner and accepts only an entity whose GetWorld is a game
    world. GetWorld is the entity's registration world (entity+0x120), and every entity the
    create path makes on the game channel is registered in the game world - so a create record
    for the UI definition (0x04000000000001F1) is world-owned without any attach. The client's
    own UI entity is owned by the client's global root, which is why the menu-shape mount above
    (20E4 on the controller) never draws the widgets in a match. The retail shape mounts here:
    041A -> its gate (BooleanSwitch, branch 0; the five-term OR opens on the wire - the possession
    term answers true for an entity whose GetWorld is a game world, and 0x09BA is an entity
    variable of this very snapshot; the mode-byte term lives in 046B, which is not mounted) ->
    state 25 launches 0x120B -> its state 15 (the native STU_501733D7 layer machine - every entry
    node of the graph is networked, so only an owner-frame bit lights it) creates HUD_Root and the
    layers. 20E4 rides next to it with the begin variables the screens wait on. Same variables as
    the controller mount, wrapped at base 0 like the body's activating snapshot."""
    return chunk(_ui_mount_frame(cmfd, False, overrides=overrides), 0, 3)


def ui_hud_message(overrides: dict | None = None):
    """Ring the layer machine's message listener: the same mount re-sent as an H=0 remote-sync
    frame with the .0C47 listener's bit (m_remoteSyncNodes position 22 of 0x120B) on. In the
    client's own boot UI the message arrives natively when the game begins; on our world-owned
    entity nothing posts it (no content graph sends .0C47) and the layer machine sits waiting -
    this frame is its match-start, and the begin runs the client-side subgraph that builds
    HUD_Root. The frame lists the whole mount again (a full frame carries everything) and the
    same variables; the caller sends it through the mount's own stream."""
    return _ui_mount_frame(0, True, overrides=overrides)


def _ui_mount_frame(
    cmfd: int, ring: bool, magazine: int = 30, widgets: bool = True, overrides: dict | None = None
):
    """The world UI entity's picture: the 041A root, its layer machine, the controller-shape HUD
    graph, and the two content widgets the machine's message builds around in a real client - the
    ammo counter (0x20E7, its number is the instance variable at presence bit 2) and the hero
    panel (0x183C, one networked gate, parented under the machine's launcher slot 22). The ring
    variant is the same picture as an H=0 frame with the listener positions lit: the machine's
    match-start message and the widget's own refresh door (worknotes/hud-msg-indices S3).
    Without the widgets the picture is the bare layer mount: a full frame destroys the instances
    it does not list, which is the off half of the re-create the presenters need."""
    instances = [
        Instance(
            1,
            UI_HOST,
            active={UI_HOST_GATE: switch_data(), UI_HOST_LAUNCH: subscript(2)},
        ),
        Instance(2, UI_MACHINE, parent=(1, UI_HOST_LAUNCH), active={MACHINE_ROOT: None}),
        Instance(3, HUD, active={HUD_GATE: None}),
    ]
    if widgets:
        instances += [
            Instance(
                4,
                AMMO_WIDGET,
                # every var the widget's presenters bind: the number and its magazine (the
                # widget's own instance pool), plus the identity vars the binding list names.
                # The number is an int, exactly as in the weapon graph (live-proven there);
                # the float form crashed the client on the snapshot that carried it.
                presence={0: Int(0), 1: Int(0), 2: Int(magazine), 3: Int(magazine),
                          4: Int(magazine)},
                active={0: None, 1: switch_data(), 2: None, 3: None},
            ),
            # parented under the machine's launcher slot: the panel instance exists and the
            # launcher adopts it on the ring's message; lighting the launcher itself from the
            # snapshot (its subscript entry) crashed the client together with the float form
            Instance(5, HERO_PANEL, parent=(2, MACHINE_PANEL), active={0: None}),
        ]
    # the widget's three entity-scope bindings (the hero, the weapon, the panel slot) join the
    # begin variables and the panel's nine: a presenter whose bound var is missing asserts
    variables = dict.fromkeys(
        HUD_BEGIN_VARS + UI_HUD_VARS + PANEL_VARS + (0x2AD4, 0x73ED, 0x001F), Bool(True)
    )
    variables.update(overrides or {})  # .hudvar: the live rewrite wins over the shipped values
    if ring:
        instances[1].remote[MACHINE_MESSAGE] = None
        if widgets:
            instances[3].remote[3] = None  # the ammo counter's listener position (S3)
        return remote_frame(instances, variables)
    return owner_full_frame(cmfd, instances, variables)


def ui_mount_frame(cmfd: int, widgets: bool, overrides: dict | None = None):
    """The mount re-sent as an owner snapshot, with or without the widget instances (the .mount
    debug switch): dropping them from a full frame destroys the instances and their states, so
    sending them again runs every presenter's begin afresh."""
    return chunk(_ui_mount_frame(cmfd, False, widgets=widgets, overrides=overrides), 0, 3)


# The game mode entity's statescript, Practice Range (worknotes/mode-graph-2026-10-26.md): 0x13C0
# is the round machine, and its launcher at owner state 19 brings up the in-game widget tree
# (0x0CB6, one gate state like 0x20E4's). Every entry node of 0x13C0 is networked, so the client
# runs none of it on its own: the phases light from the wire or the round never begins.
MODE_GRAPH = Graph(0x13C0, 20, 3)
MODE_CHILDREN = (Graph(0x0CBB, 69, 13), Graph(0x0C8F, 10, 4), Graph(0x0CB6, 1, 0))
MODE_TIMER = Graph(0x0BD6, 55, 0)
MODE_COUNTDOWN = 0x0517  # the assemble countdown, entity scope in the PvP graph's pool


def world_mode_graph_chunk():
    """The mode entity's graph-info record, the same first step as the bodies and the UI entity."""
    return chunk(
        graph_create_frame([Instance(1, MODE_GRAPH), Instance(2, MODE_TIMER)]), 1, 1
    )


def mode_chunk(cmfd: int, seconds: float):
    """The activating snapshot: the bare senders and relays lit (states 0..18 minus the switches
    and stacks, which ride their own data bits), both switches on branch 0, the stacks writing
    their presence var, and all four launchers carrying children: the HUD widget tree (0x0CB6)
    under the parent's m_states index 21, the announce (0x0CBB) and re-begin (0x0C8F) listeners
    dark inside, the round timer (0x0BD6) mounted for the announce chain."""
    cbb, c8f, cb6 = MODE_CHILDREN
    frame = owner_full_frame(
        cmfd,
        [
            Instance(
                1,
                MODE_GRAPH,
                presence={0: Bool(True)},
                active={
                    0: None,
                    1: None,
                    2: switch_data(),
                    3: None,
                    4: stack_data(),
                    5: subscript(2),
                    6: None,
                    7: None,
                    8: None,
                    9: None,
                    10: None,
                    11: None,
                    12: stack_data(),
                    13: None,
                    14: subscript(5),
                    15: None,
                    16: subscript(3),
                    17: switch_data(),
                    18: None,
                    19: subscript(4),
                },
            ),
            Instance(2, cbb, parent=(1, 5)),
            Instance(3, c8f, parent=(1, 16)),
            Instance(4, cb6, parent=(1, 21), active={0: None}),
            Instance(5, MODE_TIMER, parent=(1, 14)),
        ],
        {MODE_COUNTDOWN: Float(seconds)},
    )
    return chunk(frame, 0, 3)


def picked_hero(parameters: bytes) -> int | None:
    """The hero in a pick's parameters: an 8-byte little-endian GUID with the hero type bits (the pick
    in OW2's traffic carries it that way; its exact parameter layout is not certain)."""
    for offset in range(len(parameters) - 7):
        value = int.from_bytes(parameters[offset : offset + 8], "little")
        if value >> 48 == HERO_TYPE and value in heroes():
            return value
    return None


def wanted_open(parameters: bytes) -> bool | None:
    """1360.025's one boolean parameter, or None when it is not found. The parameters end with two u32
    entities (0x7FF78AAB7D30); the boolean before them is its type byte 1 and a value byte."""
    body = parameters[:-8]
    if len(body) >= 2 and body[-2] == 1 and body[-1] in (0, 1):
        return bool(body[-1])
    return None
