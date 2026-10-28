# HUD variable field guide [1.74]

Which variables feed the hero HUD, which ones do anything, and which ones are traps. Statuses below mean **confirmed** (rewritten on a live client, change visible) or **silent** (rewritten live, no visible change); anything still a guess says so.

Related: `HERO-WEAPONS.md` (gun graphs, the debug chat, the per-hero slot tables).

## 1. Where the readouts come from

The HUD is driven by statescript pictures rather than dedicated messages:

- The **body snapshot** (second record of the hero's body entity, base-0 owner full frame, `loadout.body_full_chunk`) creates the hero-HUD graph instance (graph `0x01CF`) and the weapon-script instances (manager plus gun scripts). Each instance carries a *presence* block: one value per bit of its graph's `.01B` synced-variable list, so **bit = position in `sync_vars`**, and the entity variables ride the frame's variables block.
- The hero HUD widget instances (ammo widget, hero panel) live on the world **UI mount** (entity 4, instance `0x20E7`; entity 5, instance `0x183C`), plus the **controller** graph `0x13C1` on the controller frame. Their synced ids are 48-bit `.01B` identifiers.

Three constraints bite here:

1. The variables block is 32-bit (`w_var`). The 48-bit widget sync ids cannot enter it; on the wire a widget instance is keyed by the low 32 bits of its id (`guid_index`), so panel probes must use `id & 0xFFFFFFFF`.
2. Presence and entity vars are read only while an instance initializes. Re-sending a full frame re-syncs the live instance, and the client replays it without re-reading the values, so a rewrite looks stuck. Force an init: `.hudvar re` destroy-creates the bodies under new ids with the rewrites inside (server command, see the debug chat).
3. A rewrite must keep the variable's shipped kind (Bool stays Bool, Int stays Int); changing kinds changes the picture format, not just the value. For an id the server does not ship anywhere the kind is unknowable, so `.hudvar` demands an explicit `bool:/int:/float:/asset:` prefix there. A rejected command silently leaves the old picture up, which is easy to misread as "no effect".

## 2. The weapon counters, and why an extra gun shows

Each gun script syncs the ammo trio (var ids are stable across heroes; the bit position is per-script, `graph.presence_bit(var)` gives it):

| var | role | shipped here |
|---|---|---|
| `.001C` | activity gate: a non-zero **Int** makes the script present its weapon (counter, view model, card) | `int:50` |
| `.0035` | current ammo (the big number) | `int:30` |
| `.00C6` | magazine size (the small number after the slash) | `int:30` |

A gun script usually carries two UXPresenter states: one whose binding list includes `.0035` (the counter card) and one that binds only `.001F`/`.281C`-class vars, a bare gun image. The bare one has no finite number source, so its count comes out as the infinity glyph, and lighting every presenter draws a weapon the hero does not own; that was the soldier's "two guns". Some scripts carry a third kind, a presenter bound only to the charge var `.003D`. It presents the gun's alternate-fire card, the right-click entry on the bar, in the slot beside the weapon card (the soldier's Helix Rockets). The shipped policy lights the counter and the charge presenter and leaves the image-only card dark. For scripts with no counter at all (the melee hammer, Mercy's staff) the bare card is the readout and stays. `.281C`, a synced int on every gun script (Soldier's presence bit 3), changes nothing visible at 0, 1 or 3 and does not gate the alternate-fire card.

Full census (parsed from the `.01C` graph records; re-derive with the snippet below):

| hero | gun script | counter presenter | bare presenter(s) |
|---|---|---|---|
| Ana | `0xb94` | 66 | 65 |
| Ashe | `0x14fc` | 103 | 104 |
| Bastion | `0x12e` | 31 | 30 |
| Brigitte | `0x1248` | - | 23 |
| Cassidy | `0x1d2` | 50 | 49 |
| D.Va | `0x2b8` | 25 | 24 |
| Doomfist | `0xd0a` | 40 | 39 |
| Echo | `0x19e5` | 11 | 12 |
| Genji | `0x304` | 75 | 74 |
| Junkrat | `0x22b` | 27 | 26 |
| Lúcio | `0x776` | 47 | 46, 48 (charge card) |
| Mercy (staff) | `0x6b` | - | 45 |
| Mercy (blaster) | `0x6d` | 30 | 29 |
| Orisa | `0xbd4` | 45 | 44 |
| Pharah | `0x23` | 25 | 24 |
| Reaper | `0x19` | 31 | 30 |
| Reinhardt | `0x48` | - | 40 |
| Roadhog | `0x1c6` | 41 | 40 |
| Sigma | `0x187f` | - | 19 |
| Soldier: 76 | `0x254` | 66 | 64, 65 (charge card) |
| Symmetra | `0x157c` | 63 | 61, 62 |
| Torbjörn (gun) | `0x1683` | 32 | 31 |
| Torbjörn (turret) | `0x168c` | - | 27 |
| Tracer | `0x16` | 31 | 30 |
| Widowmaker | `0x1b` | 83 | 82 |
| Winston | `0xfe` | 66 | 65 |
| Wrecking Ball | `0x1514` | 26 | 25 |
| Zarya | `0x241` | 53 | 52 |
| Zenyatta | `0x164` | 90 | 89 |

Baptiste `0x1932`, Hanzo `0xb9`, Mei `0x5e6`, Moira `0xf76`, Sombra `0xb1b`: no UXPresenter states in the main gun script at all.

```python
from ow174.game.script import graph

g = graph.graph(0x254)
sv = list(g.sync_vars)

def dyn(o, acc):
    if isinstance(o, dict):
        if o.get("$") == "STUConfigVarDynamic":
            i = o.get("m_identifier")
            if isinstance(i, int) and i < len(sv):
                acc.append(hex(sv[i].var))
        acc.extend(v for v in map(dyn, o.values()) if isinstance(v, list))
    elif isinstance(o, list):
        for v in o:
            dyn(v, acc)
    return acc

for slot, node in enumerate(g.owner_states()):
    if node.cls.endswith("UXPresenter"):
        print(slot, sorted(set(dyn(node.fields, []))))
```

Widget-config entries reference the same `STUConfigVarDynamic` class with large identifiers (>10000, widget handles); only in-range ids are variable reads, so do not treat every one as an index.

## 2b. The ability bar lives in the body graphs

The ability icons are UXPresenter states of the hero's *ability* scripts, not of the gun
script: Soldier: 76 has them at `0x255` slot 28, `0x257` slot 16 and `0x259` slot 32; Zenyatta
across four graphs. They render once the body allow-list (SHIP_STATES) names `UXPresenter`; without that entry
the bar stays empty for every hero. The same run showed
the allow-list cannot simply be "everything": with every one-bit class active the client dies
at spawn, a state's begin action reading a preload the server does not send. The ult still
rides only the ring's percent readout; the icon slot under the ring (the soldier's Tactical
Visor, `0x259`'s presenter) is still unexplained.

## 3. The hero-HUD graph `0x01CF` pool (22 presence bits)

Single-bit rewrites at a true init, checked on Soldier and Symmetra:

| bit | var | shipped | result |
|---|---|---|---|
| 0 | `.070F` | Bool(True) | silent |
| 1 | `.0A14` | Int(0) | **confirmed: status icon selector, 0 shows the damage-boost glyph, any non-zero shows the heal glyph** (a flag, not an id; values 1/2/3/16 all render heal) |
| 2 | `.171B` | Bool(True) | silent |
| 3 | `.071D` | Bool(True) | silent |
| 4 | `.0028` | Int(0) | silent (heal-status experiment control) |
| 5-7 | `.2450` `.2451` `.2454` | Bool(True) | silent alone and as a trio |
| 8 | `.0755` | Int(250) | silent (max-health candidate; 150 left the readout unchanged) |
| 9 | `.0756` | Int(250) | silent (current-health candidate) |
| 10 | `.075D` | Int(30) | silent (ammo candidate; the digits come from the gun script instead) |
| 11 | `.697C` | Bool(True) | silent |
| 12 | `.227F` | Bool(True) | silent |
| 13 | `.1B84` | Int(50) | silent (**not** the ult percentage: the ring still reads 0% with this at 100, the charge rides another picture) |
| 14-15 | `.5B85` `.0188` | Bool(True) | silent |
| 16-17 | `.4498` `.4499` | Int(3)/Int(0) | silent (weapon-slot pair) |
| 18-20 | `.2EAB` `.2EAC` `.2EAD` | Bool(True) | silent (ability-bar trio) |
| 21 | `.01FE` | Bool(True) | silent |

The ring, the ability icons and the weapon cards are rendered by the presenters of *other* graphs (the hero HUD presenter binds some of these vars through the entity block, see below) and read their content from hero-specific sources, so the shipped 22 bits only need to be *plausible*. The one working lever in the pool is `.0A14`.

The same graph's presenter binding list (entity-scope vars, they ride the variables block): `.0735` (asset: the hero guid, swap through `.hero`), `.076C` (float 0), and the bool cluster `.2E27 .0757 .0406 .45C2 .2243 .2AD4 .73ED .001F` (identity/widget bindings, all shipped True; single overrides silent so far, and the boost-icon-source hypothesis "asset = other player's guid" is still open).

## 4. The hero-HUD layer needs its menu hero

The ring, the status icon and the ability bar initialize only when the client was told at match start who its menu hero is: the 36600 lobby message carries `MENU_HERO_KEY` (a hero name). A profile whose lobby hero is `random` sends no key and the hero-HUD layer stays dead in the world, so every HUD experiment then reads "nothing happens" no matter what is on the wire. A named hero makes the layer live, and it survives hero switches and body destroy-creates (`.hero`/`.hudvar re`).

## 5. Reproducing a verdict

1. Be in a world on a live layer (section 4; check the ult ring renders).
2. `.hudvar <id> <kind:value>` (explicit kind for ids the server does not ship), then `.hudvar re`. Without the re-create the value is a re-sync, not an init.
3. Watch the HUD in the running client after each rewrite. The console says nothing about the result: a rewrite that applies but changes no picture and a command the server rejected look identical until you check the screen yourself.
4. For weapon cards use `.hudslot <instance> <state slot> on|off` to A/B single presenters without rebuilding the server.
