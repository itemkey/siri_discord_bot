from __future__ import annotations

import unittest

from siri_bot.bunker.content import BUILTIN_PACK


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


if __name__ == "__main__":
    unittest.main()

