from __future__ import annotations

import random
import unittest
from datetime import UTC, datetime

from siri_bot.bunker.engine import (
    can_start_game,
    generate_card,
    recommended_rounds,
    selectable_reveal_stats,
    tally_votes,
)
from siri_bot.bunker.models import BunkerPlayer, BunkerSettings, GameMode, Vote, VotePolicy


def _player(user_id: int, *, host: bool = False, ready: bool = True, eliminated: bool = False) -> BunkerPlayer:
    return BunkerPlayer(
        game_id=1,
        user_id=user_id,
        display_name=f"Player {user_id}",
        is_host=host,
        ready_at=datetime.now(UTC) if ready else None,
        invited_at=None,
        joined_at=datetime.now(UTC),
        left_at=None,
        is_eliminated=eliminated,
        card=generate_card(random.Random(user_id)),
        revealed_stats=(),
        used_special_action=False,
        immune_round=None,
    )


class BunkerEngineTests(unittest.TestCase):
    def test_recommended_rounds_follow_player_ranges_and_turbo(self) -> None:
        self.assertEqual(recommended_rounds(6), 4)
        self.assertEqual(recommended_rounds(10), 5)
        self.assertEqual(recommended_rounds(14), 6)
        self.assertEqual(recommended_rounds(10, GameMode.TURBO), 4)

    def test_start_requires_minimum_players_and_non_host_ready(self) -> None:
        players = [_player(1, host=True)] + [_player(index, ready=True) for index in range(2, 6)]
        ok, message = can_start_game(players)
        self.assertFalse(ok)
        self.assertIn("минимум", message)

        players.append(_player(6, ready=False))
        ok, message = can_start_game(players)
        self.assertFalse(ok)
        self.assertIn("Player 6", message)

        players[-1] = _player(6, ready=True)
        ok, _ = can_start_game(players)
        self.assertTrue(ok)

    def test_selectable_reveal_stats_excludes_revealed_values(self) -> None:
        player = _player(1)
        player = BunkerPlayer(**{**player.__dict__, "revealed_stats": ("profession", "secret")})

        stats = selectable_reveal_stats(player)

        self.assertNotIn("profession", stats)
        self.assertNotIn("secret", stats)
        self.assertIn("item", stats)

    def test_vote_tally_handles_abstain_policy_and_ties(self) -> None:
        players = [_player(index) for index in range(1, 7)]
        votes = [
            Vote(1, 1, 1, 2, False),
            Vote(1, 1, 2, 3, False),
            Vote(1, 1, 3, None, True),
        ]

        eliminated, message = tally_votes(players, votes, VotePolicy.ABSTAIN, random.Random(1))

        self.assertIn(eliminated, {2, 3})
        self.assertIn("Ничья", message)


if __name__ == "__main__":
    unittest.main()

