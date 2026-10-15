from __future__ import annotations

import xml.etree.ElementTree as ET
import unittest

from export_logitech_profile import add_wheel_macro
from import_logitech_profile import convert_profile_xml, parse_macro


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def round_trip(name, direction, amount):
    macro = ET.Element("macro", {"name": name})

    add_wheel_macro(
        macro,
        {
            "type": "wheel",
            "name": name,
            "direction": direction,
            "amount": amount,
        },
    )

    serialized = ET.tostring(macro, encoding="utf-8")
    return parse_macro(ET.fromstring(serialized))


class LogitechWheelConversionTests(unittest.TestCase):
    def test_single_step_wheels_round_trip(self):
        cases = (
            ("Up", "vertical", 1),
            ("Down", "vertical", -1),
            ("Right", "horizontal", 1),
            ("Left", "horizontal", -1),
        )

        for name, direction, amount in cases:
            with self.subTest(direction=direction, amount=amount):
                self.assertEqual(
                    round_trip(name, direction, amount),
                    {
                        "type": "wheel",
                        "name": name,
                        "direction": direction,
                        "amount": amount,
                    },
                )

    def test_multi_step_vertical_wheels_round_trip(self):
        for amount in (5, -5):
            with self.subTest(amount=amount):
                self.assertEqual(
                    round_trip("Multi", "vertical", amount),
                    {
                        "type": "wheel",
                        "name": "Multi",
                        "direction": "vertical",
                        "amount": amount,
                    },
                )

    def test_multi_step_vertical_exports_repeated_wheel_events(self):
        macro = ET.Element("macro", {"name": "Scroll Five"})

        add_wheel_macro(
            macro,
            {
                "type": "wheel",
                "name": "Scroll Five",
                "direction": "vertical",
                "amount": 5,
            },
        )

        wheel_events = [
            element
            for element in macro.iter()
            if local_name(element.tag) == "mousewheel"
        ]

        self.assertEqual(len(wheel_events), 5)
        self.assertEqual(
            [element.get("delta") for element in wheel_events],
            ["1"] * 5,
        )

    def test_horizontal_multi_step_export_is_rejected(self):
        for amount in (2, -2):
            with self.subTest(amount=amount):
                macro = ET.Element("macro", {"name": "Horizontal"})

                with self.assertRaisesRegex(
                    ValueError,
                    "horizontal wheel amounts",
                ):
                    add_wheel_macro(
                        macro,
                        {
                            "type": "wheel",
                            "name": "Horizontal",
                            "direction": "horizontal",
                            "amount": amount,
                        },
                    )

    def test_mixed_multikey_with_wheel_stays_macro(self):
        macro = ET.Element("macro", {"name": "Mixed"})
        multikey = ET.SubElement(macro, "multikey")

        ET.SubElement(
            multikey,
            "key",
            {"direction": "down", "value": "A"},
        )
        ET.SubElement(
            multikey,
            "mousewheel",
            {"delta": "1"},
        )
        ET.SubElement(
            multikey,
            "key",
            {"direction": "up", "value": "A"},
        )

        result = parse_macro(macro)

        self.assertEqual(result["type"], "macro")
        self.assertEqual(
            result["steps"],
            [
                {"type": "key_down", "key": "KEY_A"},
                {
                    "type": "wheel",
                    "direction": "vertical",
                    "amount": 1,
                },
                {"type": "key_up", "key": "KEY_A"},
            ],
        )

    def test_opposite_wheel_multikey_stays_macro(self):
        macro = ET.Element("macro", {"name": "Opposite"})
        multikey = ET.SubElement(macro, "multikey")

        ET.SubElement(multikey, "mousewheel", {"delta": "-1"})
        ET.SubElement(multikey, "mousewheel", {"delta": "1"})

        result = parse_macro(macro)

        self.assertEqual(result["type"], "macro")
        self.assertEqual(
            result["steps"],
            [
                {
                    "type": "wheel",
                    "direction": "vertical",
                    "amount": -1,
                },
                {
                    "type": "wheel",
                    "direction": "vertical",
                    "amount": 1,
                },
            ],
        )

    def test_backup_assignments_are_not_imported_as_active(self):
        guid = "{11111111-2222-3333-4444-555555555555}"

        xml_data = f"""
        <profiles>
          <profile name="Backup Assignment Test">
            <macros>
              <macro name="Scroll Right" guid="{guid}">
                <mousefunction>
                  <do task="scrollright"/>
                </mousefunction>
              </macro>
            </macros>
            <assignments devicecategory="Logitech.Gaming.LeftHandedController">
              <assignment shiftstate="1" contextid="G1" macroguid="{guid}" backup="true"/>
              <assignment shiftstate="1" contextid="G2" macroguid="{guid}" backup="false"/>
              <assignment shiftstate="1" contextid="G3" macroguid="{guid}"/>
            </assignments>
          </profile>
        </profiles>
        """.encode("utf-8")

        result = convert_profile_xml(
            xml_data,
            source_name="backup-assignment-test.xml",
        )

        layer = result["layers"]["1"]

        self.assertNotIn("G1", layer)
        self.assertIn("G2", layer)
        self.assertIn("G3", layer)

        for context_id in ("G2", "G3"):
            with self.subTest(context_id=context_id):
                self.assertEqual(
                    layer[context_id]["type"],
                    "wheel",
                )
                self.assertEqual(
                    layer[context_id]["direction"],
                    "horizontal",
                )
                self.assertEqual(
                    layer[context_id]["amount"],
                    1,
                )

        self.assertIn(
            guid,
            result["commands"],
        )

if __name__ == "__main__":
    unittest.main(verbosity=2)
