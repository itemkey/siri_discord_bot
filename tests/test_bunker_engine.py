from __future__ import annotations

import random
import unittest
from datetime import UTC, datetime

from siri_bot.bunker.engine import (
    can_start_game,
    assign_cards,
    generate_card,
    normalize_settings,
    next_state_after_timer,
    phase_deadline,
    recommended_rounds,
    reveal_stat,
    selectable_reveal_stats,
    tally_votes,
)
from siri_bot.bunker.content import ContentPack
from siri_bot.bunker.models import BunkerPlayer, BunkerSettings, GameMode, GameState, RoomKind, Vote, VotePolicy


def _player(user_id: int, *, host: bool = False, ready: bool = True, eliminated: bool = False, fake: bool = False) -> BunkerPlayer:
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
        is_fake=fake,
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

    def test_fake_players_count_toward_start_gate_when_ready(self) -> None:
        players = [_player(1, host=True)] + [_player(-index, ready=True, fake=True) for index in range(2, 7)]

        ok, _ = can_start_game(players)

        self.assertTrue(ok)

    def test_ranked_start_rejects_fake_players(self) -> None:
        players = [_player(1, host=True)] + [_player(-index, ready=True, fake=True) for index in range(2, 7)]

        ok, message = can_start_game(players, ranked=True)

        self.assertFalse(ok)
        self.assertIn("Ranked", message)

    def test_assign_cards_uses_selected_content_pack(self) -> None:
        pack = ContentPack(
            professions=("Кастомный инженер",),
            items=("Кастомный предмет",),
            weaknesses=("Кастомная слабость",),
            secrets=("Кастомный секрет",),
            skills=("Кастомный навык",),
            phobias=("Кастомная фобия",),
            funny_traits=("Кастомная черта",),
            apocalypses=("Кастомный апокалипсис",),
            bunker_defects=("Кастомный дефект",),
            chaos_events=("Кастомный хаос",),
            layouts=("Кастомная планировка",),
            special_actions=("Кастомное действие",),
        )
        players = [_player(1, host=True), _player(2)]

        cards = assign_cards(players, BunkerSettings(), random.Random(1), pack)

        self.assertEqual(cards[1].profession, "Кастомный инженер")
        self.assertEqual(cards[2].inventory, "Кастомный предмет")
        self.assertEqual(len(cards[2].special_abilities), 2)

    def test_selectable_reveal_stats_excludes_revealed_values(self) -> None:
        player = _player(1)
        player = BunkerPlayer(**{**player.__dict__, "revealed_stats": ("gender", "profession", "fact")})

        stats = selectable_reveal_stats(player)

        self.assertEqual(stats[0], "body")
        self.assertNotIn("gender", stats)
        self.assertNotIn("profession", stats)
        self.assertNotIn("fact", stats)
        self.assertIn("inventory", stats)

    def test_reveal_requires_ordered_card_schema(self) -> None:
        player = _player(1)

        ok, message = reveal_stat(player, "profession")

        self.assertFalse(ok)
        self.assertIn("Пол", message)

        ok, message = reveal_stat(player, "gender")

        self.assertTrue(ok)
        self.assertIn("Пол", message)

    def test_reveal_phase_is_not_timer_driven(self) -> None:
        settings = BunkerSettings()

        self.assertIsNone(phase_deadline(settings, GameState.REVEAL_PHASE, datetime.now(UTC)))
        self.assertEqual(next_state_after_timer(GameState.REVEAL_PHASE), GameState.REVEAL_PHASE)

    def test_old_casual_settings_normalize_to_ranked(self) -> None:
        settings = BunkerSettings.from_json({"room_kind": "casual", "is_ranked": False})

        self.assertEqual(settings.room_kind, RoomKind.RANKED)
        self.assertTrue(settings.is_ranked)

    def test_ranked_settings_restore_competitive_min_players(self) -> None:
        settings = normalize_settings(BunkerSettings(room_kind=RoomKind.RANKED, min_players=1, is_ranked=False))

        self.assertEqual(settings.min_players, 6)
        self.assertTrue(settings.is_ranked)

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

    def test_vote_tally_allows_self_vote(self) -> None:
        players = [_player(index) for index in range(1, 7)]
        votes = [Vote(1, 1, 1, 1, False)]

        eliminated, _ = tally_votes(players, votes, VotePolicy.ABSTAIN, random.Random(1))

        self.assertEqual(eliminated, 1)


if __name__ == "__main__":
    unittest.main()
