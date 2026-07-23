from __future__ import annotations

import json
import unittest

from siri_bot.bunker.content import PACK_FIELDS
from siri_bot.bunker.pack_format import (
    PACK_FILE_FORMAT,
    PACK_FILE_VERSION,
    dump_pack_file,
    pack_file_payload,
    pack_file_template,
    parse_pack_file,
)


class BunkerPackFormatTests(unittest.TestCase):
    def test_v1_pack_file_round_trips_through_dump_and_parse(self) -> None:
        raw = dump_pack_file(
            name="Городской пак",
            description="Для редактора",
            content={
                "professions": ["  Инженер  ", "Инженер", ""],
                "ages": ["42 года"],
                "genders": ["женщина"],
                "names": ["Мария"],
                "surnames": ["Морозова"],
                "appearances": ["спокойное лицо"],
                "clothing": ["рабочая куртка"],
                "biology": ["может иметь детей"],
                "items": ["Фильтр воды"],
                "large_items": ["переносной генератор"],
            },
        )

        parsed = parse_pack_file(raw.encode("utf-8"))
        payload = json.loads(raw)

        self.assertEqual(payload["format"], PACK_FILE_FORMAT)
        self.assertEqual(payload["version"], PACK_FILE_VERSION)
        self.assertEqual(parsed.name, "Городской пак")
        self.assertEqual(parsed.description, "Для редактора")
        self.assertEqual(parsed.content["professions"], ("Инженер",))
        self.assertEqual(parsed.content["ages"], ("42 года",))
        self.assertEqual(parsed.content["genders"], ("женщина",))
        self.assertEqual(parsed.content["names"], ("Мария",))
        self.assertEqual(parsed.content["surnames"], ("Морозова",))
        self.assertEqual(parsed.content["appearances"], ("спокойное лицо",))
        self.assertEqual(parsed.content["clothing"], ("рабочая куртка",))
        self.assertEqual(parsed.content["biology"], ("может иметь детей",))
        self.assertEqual(parsed.content["items"], ("Фильтр воды",))
        self.assertEqual(parsed.content["large_items"], ("переносной генератор",))
        self.assertTrue(all(field in parsed.content for field in PACK_FIELDS))

    def test_legacy_named_content_payload_is_accepted(self) -> None:
        parsed = parse_pack_file(
            json.dumps(
                {
                    "name": "Legacy",
                    "description": "old",
                    "content": {"professions": ["Врач"], "items": ["Аптечка"]},
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(parsed.name, "Legacy")
        self.assertEqual(parsed.description, "old")
        self.assertEqual(parsed.content["professions"], ("Врач",))
        self.assertEqual(parsed.content["items"], ("Аптечка",))
        self.assertEqual(parsed.content["ages"], ())

    def test_raw_category_payload_is_accepted(self) -> None:
        parsed = parse_pack_file('{"professions": ["Пилот"], "items": ["Компас"]}')

        self.assertIsNone(parsed.name)
        self.assertIsNone(parsed.description)
        self.assertEqual(parsed.content["professions"], ("Пилот",))
        self.assertEqual(parsed.content["items"], ("Компас",))

    def test_unknown_category_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Неизвестные категории"):
            parse_pack_file('{"unknown": ["value"]}')

    def test_invalid_format_and_version_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Неизвестный формат"):
            parse_pack_file('{"format": "other", "version": 1, "content": {}}')

        with self.assertRaisesRegex(ValueError, "Неподдерживаемая версия"):
            parse_pack_file(f'{{"format": "{PACK_FILE_FORMAT}", "version": 2, "content": {{}}}}')

    def test_structured_special_actions_are_normalized(self) -> None:
        payload = pack_file_payload(
            name="Abilities",
            content={
                "special_actions": [
                    {
                        "id": "heal",
                        "name": "Медик",
                        "description": "Перебросить здоровье",
                        "effect": "reroll_stat",
                        "target": "self",
                        "stat_key": "health",
                        "uses": 1,
                        "timing": "reveal_or_discussion",
                    }
                ]
            },
        )

        self.assertIsInstance(payload["content"]["special_actions"][0], dict)
        parsed = parse_pack_file(json.dumps(payload, ensure_ascii=False))
        ability = json.loads(parsed.content["special_actions"][0])

        self.assertEqual(ability["id"], "heal")
        self.assertEqual(ability["effect"], "reroll_stat")
        self.assertEqual(ability["stat_key"], "health")

    def test_composite_special_actions_round_trip_actions(self) -> None:
        payload = pack_file_payload(
            name="Composite",
            content={
                "special_actions": [
                    {
                        "id": "field_medic",
                        "name": "Полевой медик",
                        "description": "Перебросить здоровье и получить защиту на раунд.",
                        "effect": "reroll_stat",
                        "target": "self",
                        "stat_key": "health",
                        "uses": 1,
                        "timing": "reveal_or_discussion",
                        "actions": [
                            {"effect": "reroll_stat", "target": "self", "stat_key": "health"},
                            {"effect": "exile_immunity", "target": "self"},
                        ],
                    }
                ]
            },
        )

        exported_ability = payload["content"]["special_actions"][0]
        self.assertIsInstance(exported_ability, dict)
        self.assertEqual(len(exported_ability["actions"]), 2)

        parsed = parse_pack_file(json.dumps(payload, ensure_ascii=False))
        ability = json.loads(parsed.content["special_actions"][0])

        self.assertEqual(ability["id"], "field_medic")
        self.assertEqual(ability["actions"][0]["effect"], "reroll_stat")
        self.assertEqual(ability["actions"][1]["effect"], "exile_immunity")

    def test_composite_special_actions_reject_unknown_nested_effect(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown special ability effect"):
            pack_file_payload(
                name="Bad",
                content={
                    "special_actions": [
                        {
                            "id": "bad",
                            "name": "Bad",
                            "effect": "generic_note",
                            "actions": [{"effect": "teleport", "target": "self"}],
                        }
                    ]
                },
            )

    def test_template_is_valid_v1_file(self) -> None:
        parsed = parse_pack_file(pack_file_template())

        self.assertEqual(parsed.name, "Новый пак Бункера")
        self.assertTrue(all(field in parsed.content for field in PACK_FIELDS))


if __name__ == "__main__":
    unittest.main()
