# Hero guns and the in-game HUD (1.74)

What it takes for a scripted hero body (see [STATESCRIPT.md](STATESCRIPT.md)) to show its weapon
and its HUD. Every gate below was read out of the client and confirmed by a live render; the
encoders are in `ow174/game/loadout.py`, `ow174/game/heroselect.py` and
`ow174/lobby/matchmaker.py`, behind `--hero-scripts`.

## A body picture alone shows gloves

The statescript component lists the graphs an entity runs, and a body's picture normally names
only the body graphs. The view model lives in the hero's weapon script graphs (Soldier: 76's
0x254 builds its gun through a `CosmeticEntity` state) and those run in a manager graph (0x15):
a snapshot that shows no gun is missing instances, not cosmetics. The working picture adds:

- the manager instance, with its two weapon-management states on (the states named by the graph's
  `m_states` for classes `CD46AF93` and `BD02E168`, at bit positions 29 and 33 of the instance
  list). These are bare-bit states; the pair was verified against a live 1.23 server first, and
  the same two states build the cosmetics there;
- every weapon script instance of the hero, with all its `CosmeticEntity` states on (one bit each).

The gun definition the cosmetic state instantiates is looked up in the 20505 preload list: a
weapon graph that is on but whose definition was never preloaded builds nothing, silently. The
captured 1.68 skin list this server ships does not carry gun definitions, so `loadout.gun_defs()`
appends the hero's own definitions to the skins list of the same 20505 record (list entry at
`+0x90`).

## The ammo number

The script's ammo counter is an entity sync var: for 0x254 it is var 53 (id 0x0035), presence bit
8 of its instance entry, value a full magazine (30). The widget re-reads the var whenever a
snapshot writes it - the write-up hook runs from the frame reader's var loop, at
0x7FF78A1B2DC0 (1.74) - so the counter has its number before the first shot, and a BeginAction
on the script is not needed to light it.

Firing is the script's own `WeaponVolley` state (for 0x254, state 10 of the weapon instance,
instance 11 of the picture): the two-bit form documented in STATESCRIPT.md, driven from button
32 of the player's command record. A press lights the volley, a release stops it.

## The HUD needs a world-owned entity, and the mode machine needs mode 5

Two independent gates decide whether the in-game HUD draws, and both are native:

1. **Owner.** The widget visibility predicate resolves through the UI instance's owner entity and
   accepts only an entity registered in a game world - registration is what makes one: the create
   path stores the world pointer into entity `+0x120`, which is what `GetWorld` answers. The UI
   entity the client creates at boot is owned by the client's global root, so the menu-shape HUD
   mount never draws in a match. The server must create its own UI entity (definition
   `0x04000000000001F1`) in the match snapshot and mount the HUD graph on it, with the begin
   variables the HUD screens wait on as entity variables: `0x09BA` (the layer machine's switch
   term), `0x401F` (the dispatcher's preload var) and `0x09FC` (the mode screen's).
2. **Mode.** `HUD_Root` itself is created by the mode machine of UI graph `120B`, and its gate is
   a UI-entity variable the UI manager sets only while the client sits in **mode 5** - the queue
   "match found" state. Mode 5 is entered by the group banner (message 44203) and leaves with it,
   which is why a direct match join never shows a HUD: the client is not in the mode. The test
   join (`--test-map`) therefore rides the queue: the match first joins the queue (44201 plus the
   party-state entry), the banner answers the client's Accept (44104) when it comes and otherwise
   waits out its window, and the join happens at the commit.

One trap on that path: a party state still carrying the queue entry makes the client offer role
selection again while loading into the world, so the entry must be removed from the party state
(a 20700 without it) at the commit - the banner itself dies by its own deadline or by the accept,
not by the party state.

## Open threads

What is known but not implemented, to continue from:

- **Round phase.** Fire-while-reloading and the round countdown read a game-mode statescript
  graph (0x13C0 in the Practice Range, 0x0F4A in quick play). The incoming dispatcher of channel
  1 has no 21600-base handler: round state travels in statescript, and no mode-graph snapshot is
  sent yet, so the client sits in a permanent pre-round.
- **Counter widgets.** The ammo and health readouts bind through widget 0x20E7 with a
  `.025:0x1005` refresh term; the sync-var write hook is documented above, but the readout layer
  of the HUD screens is not fully mounted yet, so the numbers exist (visible on the gun's own
  display) and do not reach the HUD counters.
- **Multi-weapon heroes.** Heroes with swappable weapons (Torbjorn) take both weapons' cosmetic
  states from one picture and render them fused; the states need to be split by the active
  weapon, not all lit at once.
- **Muzzle flash and tracers** ride state transitions of the WEAPON graphs, which need the Anim
  states and their type-3 preloads mounted.
