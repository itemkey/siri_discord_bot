from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from siri_bot.leveling.formula import (
    FormulaConfig,
    first_place_changed,
    is_cooldown_available,
    level_for_total_xp,
    progress_for_total_xp,
    resolve_booster_multiplier,
    reward_roles_for_level,
    xp_for_next_level,
)


class LevelingFormulaTests(unittest.TestCase):
    def test_quadratic_thresholds_and_levels(self) -> None:
        config = FormulaConfig()

        self.assertEqual(xp_for_next_level(0, config), 100)
        self.assertEqual(xp_for_next_level(1, config), 155)
        self.assertEqual(level_for_total_xp(99, config), 0)
        self.assertEqual(level_for_total_xp(100, config), 1)
        self.assertEqual(level_for_total_xp(254, config), 1)
        self.assertEqual(level_for_total_xp(255, config), 2)

    def test_progress_for_total_xp(self) -> None:
        progress = progress_for_total_xp(120, FormulaConfig())

        self.assertEqual(progress.level, 1)
        self.assertEqual(progress.current_level_xp, 20)
        self.assertEqual(progress.next_level_xp, 155)
        self.assertEqual(progress.total_xp, 120)

    def test_linear_formula(self) -> None:
        config = FormulaConfig(preset="linear", a=99, b=10, c=50)

        self.assertEqual(xp_for_next_level(0, config), 50)
        self.assertEqual(xp_for_next_level(3, config), 80)

    def test_reward_roles_accumulative(self) -> None:
        rewards = [(1, 10), (5, 50), (10, 100)]

        self.assertEqual(reward_roles_for_level(rewards, 5, "accumulative"), {10, 50})

    def test_reward_roles_highest_only(self) -> None:
        rewards = [(1, 10), (5, 50), (10, 100)]

        self.assertEqual(reward_roles_for_level(rewards, 7, "highest_only"), {50})
        self.assertEqual(reward_roles_for_level(rewards, 0, "highest_only"), set())

    def test_booster_multiplier_uses_best_scope_and_cap(self) -> None:
        rows = [
            {"scope": "global", "multiplier": 2.0},
            {"scope": "user", "multiplier": 1.5},
            {"scope": "role", "multiplier": 2.0},
            {"scope": "role", "multiplier": 3.0},
        ]

        self.assertEqual(resolve_booster_multiplier(rows), 5.0)

    def test_cooldown_availability(self) -> None:
        now = datetime.now(UTC)

        self.assertTrue(is_cooldown_available(now, None))
        self.assertTrue(is_cooldown_available(now, now - timedelta(seconds=1)))
        self.assertFalse(is_cooldown_available(now, now + timedelta(seconds=1)))

    def test_first_place_changed(self) -> None:
        self.assertFalse(first_place_changed(1, 1))
        self.assertTrue(first_place_changed(1, 2))
        self.assertTrue(first_place_changed(None, 2))


if __name__ == "__main__":
    unittest.main()

