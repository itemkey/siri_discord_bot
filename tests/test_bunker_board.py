from __future__ import annotations

import unittest
from datetime import UTC, datetime

from siri_bot.bunker.board import render_board_png
from siri_bot.bunker.engine import generate_profile
from siri_bot.bunker.models import BunkerGame, BunkerPlayer, BunkerSettings, GameState


class BunkerBoardTests(unittest.TestCase):
    def test_board_renderer_returns_png_bytes(self) -> None:
        settings = BunkerSettings()
        game = BunkerGame(
            id=1,
            guild_id=1,
            setup_id=1,
            setup_channel_id=10,
            setup_message_id=20,
            category_id=30,
            game_text_channel_id=40,
            voice_channel_id=50,
            host_id=100,
            state=GameState.LOBBY,
            settings=settings,
            round_number=0,
            phase_started_at=None,
            phase_ends_at=None,
            paused_at=None,
            board_message_id=60,
            profile=generate_profile(settings),
            recent_events=("Бункер построен.",),
            finished_at=None,
        )
        player = BunkerPlayer(
            game_id=1,
            user_id=100,
            display_name="Хост",
            is_host=True,
            ready_at=datetime.now(UTC),
            invited_at=None,
            joined_at=datetime.now(UTC),
            left_at=None,
            is_eliminated=False,
            card=None,
            revealed_stats=(),
            used_special_action=False,
            immune_round=None,
        )

        data = render_board_png(game, [player])

        self.assertTrue(data.startswith(b"\x89PNG"))
        self.assertGreater(len(data), 1000)


if __name__ == "__main__":
    unittest.main()

