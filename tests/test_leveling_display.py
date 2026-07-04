from __future__ import annotations

import unittest

from siri_bot.cogs.leveling import _leaderboard_entry_line


class LevelingDisplayTests(unittest.TestCase):
    def test_leaderboard_entry_line(self) -> None:
        self.assertEqual(_leaderboard_entry_line(3, "@User", 7, 1234), "#3 @User - level 7, 1234 XP")


if __name__ == "__main__":
    unittest.main()
