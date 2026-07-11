from __future__ import annotations

import unittest

from siri_bot.bunker.content import BUILTIN_PACK, ContentPack, merge_content_packs, normalize_pack_content


class BunkerContentTests(unittest.TestCase):
    def test_builtin_pack_has_required_starting_content(self) -> None:
        counts = BUILTIN_PACK.counts()

        self.assertGreaterEqual(counts["professions"], 8)
        self.assertGreaterEqual(counts["ages"], 50)
        self.assertGreaterEqual(counts["genders"], 3)
        self.assertGreaterEqual(counts["items"], 8)
        self.assertGreaterEqual(counts["weaknesses"], 8)
        self.assertGreaterEqual(counts["secrets"], 8)
        self.assertGreaterEqual(counts["skills"], 8)
        self.assertGreaterEqual(counts["phobias"], 8)
        self.assertGreaterEqual(counts["funny_traits"], 8)
        self.assertGreaterEqual(counts["biology"], 5)
        self.assertGreaterEqual(counts["apocalypses"], 5)
        self.assertGreaterEqual(counts["bunker_defects"], 5)
        self.assertGreaterEqual(counts["chaos_events"], 5)
        self.assertEqual(counts["special_actions"], 8)

    def test_custom_pack_content_is_normalized_and_merged_with_builtin(self) -> None:
        content = normalize_pack_content(
            {
                "professions": ["  Свой инженер  ", "Свой инженер", ""],
                "ages": ["99 лет"],
                "genders": ["свой пол"],
                "biology": ["своя биология"],
                "items": ["Личный фильтр"],
            }
        )
        custom = ContentPack.from_json(content)

        merged = merge_content_packs(BUILTIN_PACK, custom)

        self.assertIn("Свой инженер", merged.professions)
        self.assertIn("99 лет", merged.ages)
        self.assertIn("свой пол", merged.genders)
        self.assertIn("своя биология", merged.biology)
        self.assertIn("Личный фильтр", merged.items)
        self.assertGreater(len(merged.professions), len(custom.professions))

    def test_unknown_pack_category_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_pack_content({"unknown": ["value"]})

    def test_builtin_pack_is_neutral_not_meme_pack(self) -> None:
        raw = "\n".join(value for values in BUILTIN_PACK.to_json().values() for value in values).casefold()

        for banned in ("мем", "кринж", "майонез", "караоке", "ложк", "крысы"):
            self.assertNotIn(banned, raw)

    def test_builtin_pack_has_no_artificial_generated_tails(self) -> None:
        raw = "\n".join(value for values in BUILTIN_PACK.to_json().values() for value in values).casefold()

        for banned in (
            "медицинская отметка",
            "ресурсная ценность",
            "практический уровень",
            "подтверждение в личном деле",
            "протокол события",
        ):
            self.assertNotIn(banned, raw)


if __name__ == "__main__":
    unittest.main()
