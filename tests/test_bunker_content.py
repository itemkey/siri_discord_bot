from __future__ import annotations

import unittest

from siri_bot.bunker.content import BUILTIN_PACK, ContentPack, merge_content_packs, normalize_pack_content


class BunkerContentTests(unittest.TestCase):
    def test_builtin_pack_has_required_starting_content(self) -> None:
        counts = BUILTIN_PACK.counts()

        self.assertGreaterEqual(counts["professions"], 100)
        self.assertGreaterEqual(counts["items"], 100)
        self.assertGreaterEqual(counts["weaknesses"], 100)
        self.assertGreaterEqual(counts["secrets"], 100)
        self.assertGreaterEqual(counts["apocalypses"], 50)
        self.assertGreaterEqual(counts["bunker_defects"], 50)
        self.assertGreaterEqual(counts["chaos_events"], 100)
        self.assertEqual(counts["special_actions"], 8)

    def test_custom_pack_content_is_normalized_and_merged_with_builtin(self) -> None:
        content = normalize_pack_content(
            {
                "professions": ["  Свой инженер  ", "Свой инженер", ""],
                "items": ["Личный фильтр"],
            }
        )
        custom = ContentPack.from_json(content)

        merged = merge_content_packs(BUILTIN_PACK, custom)

        self.assertIn("Свой инженер", merged.professions)
        self.assertIn("Личный фильтр", merged.items)
        self.assertGreater(len(merged.professions), len(custom.professions))

    def test_unknown_pack_category_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_pack_content({"unknown": ["value"]})

    def test_builtin_pack_is_neutral_not_meme_pack(self) -> None:
        raw = "\n".join(value for values in BUILTIN_PACK.to_json().values() for value in values).casefold()

        for banned in ("мем", "кринж", "майонез", "караоке", "ложк", "крысы"):
            self.assertNotIn(banned, raw)


if __name__ == "__main__":
    unittest.main()
