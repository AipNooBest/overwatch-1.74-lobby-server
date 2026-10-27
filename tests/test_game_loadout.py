"""Hero body scripts: the frames a fresh body gets, at the sizes the live client accepted."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ow174.game import loadout, statescript
from ow174.game.content import heroes


def bitstring(bits) -> str:
    return "".join(str((bits.value >> k) & 1) for k in range(bits.count))


class LoadoutTests(unittest.TestCase):
    def test_every_described_body_builds_the_pair_of_chunks(self):
        for body in sorted(loadout.graph.bodies()):
            create = loadout.body_graph_chunk(body)
            full = loadout.body_full_chunk(body, 9)
            self.assertIsNotNone(create, f"body {body:016X}")
            self.assertIsNotNone(full, f"body {body:016X}")
            self.assertLess(create.count, full.count)  # the empty frame is a prefix step, not the picture

    def test_the_create_chunk_carries_no_instances(self):
        # The create record is the empty graph-info frame in a base-1 chunk: header, size varint,
        # lead/H/C and CmFD 0, then only the empty instance list - 56 bits of nothing but structure.
        create = loadout.body_graph_chunk(0x0400000000002593)  # Echo
        self.assertEqual(create.count, 56)
        self.assertEqual(
            bitstring(create),
            "01000000000000000010000000000010100001000000000000000000",
        )

    def test_hero_frames_are_the_sizes_the_live_run_proved(self):
        # Unrestricted frames per hero body: instance counts (body graphs plus the manager, the
        # weapon scripts that build the gun and the hero HUD the dispatcher launches), entity
        # vars and the full snapshot's bit size (the numbers a regression to the encoder must
        # not change).
        expected = {
            "Echo": (11, 19, 2265, 2310),
            "Genji": (11, 20, 2274, 2319),
            "Soldier: 76": (12, 17, 2032, 2077),
            "Wrecking Ball": (13, 22, 2540, 2585),
        }
        for name, (graphs, evars, frame_bits, full_bits) in expected.items():
            hero = next(h for h in heroes().values() if h.name == name)
            instances, entity_vars = loadout.body_instances(hero.body, allow=None)
            self.assertEqual(len(instances), graphs, name)
            self.assertEqual(len(entity_vars), evars, name)
            self.assertEqual(loadout.body_frame(hero.body, 1, allow=None).count, frame_bits, name)
            self.assertEqual(loadout.body_full_chunk(hero.body, 9, allow=None).count, full_bits, name)

    def test_the_hero_asset_and_the_hud_gates_ride_the_entity_block(self):
        hero = next(h for h in heroes().values() if h.name == "Echo")
        _, entity_vars = loadout.body_instances(hero.body, allow=None)
        asset = entity_vars[0x03AC]  # every StateHero reads this; a null guid and the client exits
        self.assertIsInstance(asset, statescript.Asset)
        self.assertEqual(asset.guid, hero.guid)
        # no hero body in the data carries 20E4: the HUD's begin-condition vars belong to the
        # controller's frames (see heroselect), and the body snapshot must not invent them here
        self.assertNotIn(17906, entity_vars)

    def test_the_volley_target_points_at_the_gun(self):
        # The firing frames address the gun's WeaponVolley state by (instance, slot): Soldier: 76
        # keeps it in his rifle script graph, which rides as its own instance - after the nine body
        # graphs and the manager - at owner state 10.
        hero = next(h for h in heroes().values() if h.name == "Soldier: 76")
        self.assertEqual(loadout.volley_target(hero.body), (11, 10))
        self.assertIsNone(loadout.volley_target(0))

    def test_a_volley_entry_grows_the_frame_by_exactly_its_form(self):
        # Baseline: the state is one off-bit. The volley frame: has-data + active + the 18 bits.
        hero = next(h for h in heroes().values() if h.name == "Soldier: 76")
        base = loadout.body_full_chunk(hero.body, 9, allow=loadout.SHIP_STATES).count
        volley = loadout.body_full_chunk(
            hero.body, 9, allow=loadout.SHIP_STATES,
            entries={(11, 10): statescript.HasData(statescript.volley_data())},
        ).count
        self.assertEqual(volley, base + 19)  # +2 for the two-bit entry, +18 data, -1 off bit

    def test_the_state_allow_list_keeps_structure_and_shrinks_the_picture(self):
        # The shipped allow-list keeps every graph and launcher and the gun scripts, and drops a few
        # state payloads: the same body picture, minus the classes whose begin actions need
        # encoders that are not here yet.
        instances, entity_vars = loadout.body_instances(0x0400000000002593, allow=loadout.SHIP_STATES)
        self.assertEqual(len(instances), 11)  # the graphs stay: launchers and gates are structure
        self.assertEqual(len(entity_vars), 19)
        self.assertEqual(loadout.body_frame(0x0400000000002593, 1, allow=loadout.SHIP_STATES).count, 2205)
        launchers = [
            slot for instance in instances for slot in instance.active
            if instance.graph.index != statescript.HUD.index
        ]
        self.assertTrue(launchers)  # the SubScript launchers remain on

    def test_a_hud_var_rewrite_wins_over_the_shipped_values(self):
        # .hudvar: the override writes the same presence bit the shipped value does, and a
        # variable the hero HUD graph does not sync rides the entity block instead. Replacing an
        # int by an int keeps the frame the size the live run proved (the kind must not change).
        hero = next(h for h in heroes().values() if h.name == "Soldier: 76")
        bit = loadout.graph.graph(loadout.HERO_HUD_GRAPH).presence_bit(0x1B84)
        instances, entity_vars = loadout.body_instances(
            hero.body, allow=None,
            hud_vars={0x1B84: statescript.Int(100), 0x2E27: statescript.Bool(False)},
        )
        hud_instance = next(i for i in instances if i.graph.index == loadout.HERO_HUD_GRAPH)
        self.assertEqual(hud_instance.presence[bit], statescript.Int(100))
        self.assertEqual(entity_vars[0x2E27], statescript.Bool(False))
        self.assertEqual(
            loadout.body_full_chunk(hero.body, 9, hud_vars={0x1B84: statescript.Int(100)}).count,
            loadout.body_full_chunk(hero.body, 9).count,
        )

    def test_a_hud_var_rewrite_reaches_the_weapon_scripts(self):
        # The weapon scripts sync their own counters (.0035); the rewrite reaches them through
        # the same dict, so the ammo digits can be tested live without a code change.
        hero = next(h for h in heroes().values() if h.name == "Soldier: 76")
        instances, _ = loadout.body_instances(
            hero.body, allow=None, hud_vars={loadout.AMMO_VAR: statescript.Int(7)}
        )
        carriers = [
            instance for instance in instances
            if (bit := loadout.graph.graph(instance.graph.index).presence_bit(loadout.AMMO_VAR))
            is not None and instance.presence.get(bit) == statescript.Int(7)
        ]
        self.assertTrue(carriers)

    def test_the_hud_var_spec_parses_in_the_shipped_kind(self):
        self.assertEqual(loadout.parse_hud_value("0x1B84 100"), (0x1B84, statescript.Int(100)))
        self.assertEqual(loadout.parse_hud_value("2EAB off"), (0x2EAB, statescript.Bool(False)))
        self.assertEqual(loadout.parse_hud_value("2EAB on"), (0x2EAB, statescript.Bool(True)))
        self.assertEqual(loadout.parse_hud_value("06C4 99.5"), (0x06C4, statescript.Float(99.5)))
        self.assertEqual(loadout.parse_hud_value("1B84 float:7"), (0x1B84, statescript.Float(7.0)))
        self.assertEqual(loadout.parse_hud_value("0756 int:250"), (0x0756, statescript.Int(250)))
        self.assertEqual(
            loadout.parse_hud_value("2450 bool:0"), (0x2450, statescript.Bool(False))
        )
        with self.assertRaises(ValueError):
            loadout.parse_hud_value("0x9999 1")  # unknown id: say the kind explicitly


if __name__ == "__main__":
    unittest.main()
