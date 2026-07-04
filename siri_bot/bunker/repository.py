from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import asyncpg

from siri_bot.bunker.models import (
    BunkerGame,
    BunkerGuildSettings,
    BunkerPlayer,
    BunkerProfile,
    BunkerSettings,
    CharacterCard,
    GameState,
    RoomSetup,
    Vote,
)


class BunkerRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def init_schema(self) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bunker_room_setups (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    setup_channel_id BIGINT NOT NULL UNIQUE,
                    category_id BIGINT,
                    setup_message_id BIGINT,
                    room_name TEXT NOT NULL,
                    active_game_id BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS bunker_host_drafts (
                    setup_id BIGINT NOT NULL REFERENCES bunker_room_setups(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    settings JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (setup_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS bunker_guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    operator_role_id BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS bunker_games (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    setup_id BIGINT NOT NULL REFERENCES bunker_room_setups(id) ON DELETE CASCADE,
                    setup_channel_id BIGINT NOT NULL,
                    setup_message_id BIGINT,
                    category_id BIGINT,
                    game_text_channel_id BIGINT,
                    voice_channel_id BIGINT,
                    host_id BIGINT NOT NULL,
                    state TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    is_public BOOLEAN NOT NULL,
                    slots INTEGER NOT NULL,
                    max_rounds INTEGER NOT NULL,
                    round_number INTEGER NOT NULL DEFAULT 0,
                    timer_seconds INTEGER NOT NULL,
                    phase_started_at TIMESTAMPTZ,
                    phase_ends_at TIMESTAMPTZ,
                    paused_at TIMESTAMPTZ,
                    board_message_id BIGINT,
                    settings JSONB NOT NULL,
                    bunker_profile JSONB,
                    recent_events JSONB NOT NULL DEFAULT '[]',
                    is_admin_game BOOLEAN NOT NULL DEFAULT FALSE,
                    finished_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_bunker_active_game_per_setup
                    ON bunker_games (setup_id)
                    WHERE finished_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_bunker_game_text_channel
                    ON bunker_games (game_text_channel_id)
                    WHERE finished_at IS NULL;

                CREATE TABLE IF NOT EXISTS bunker_players (
                    game_id BIGINT NOT NULL REFERENCES bunker_games(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    display_name TEXT NOT NULL,
                    is_host BOOLEAN NOT NULL DEFAULT FALSE,
                    ready_at TIMESTAMPTZ,
                    invited_at TIMESTAMPTZ,
                    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    left_at TIMESTAMPTZ,
                    is_eliminated BOOLEAN NOT NULL DEFAULT FALSE,
                    card JSONB,
                    revealed_stats JSONB NOT NULL DEFAULT '[]',
                    used_special_action BOOLEAN NOT NULL DEFAULT FALSE,
                    immune_round INTEGER,
                    personal_bonus INTEGER NOT NULL DEFAULT 0,
                    is_fake BOOLEAN NOT NULL DEFAULT FALSE,
                    PRIMARY KEY (game_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS bunker_votes (
                    game_id BIGINT NOT NULL REFERENCES bunker_games(id) ON DELETE CASCADE,
                    round_number INTEGER NOT NULL,
                    voter_id BIGINT NOT NULL,
                    target_user_id BIGINT,
                    is_abstain BOOLEAN NOT NULL DEFAULT FALSE,
                    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (game_id, round_number, voter_id)
                );

                CREATE TABLE IF NOT EXISTS bunker_game_events (
                    id BIGSERIAL PRIMARY KEY,
                    game_id BIGINT NOT NULL REFERENCES bunker_games(id) ON DELETE CASCADE,
                    round_number INTEGER NOT NULL DEFAULT 0,
                    event_type TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            await connection.execute(
                """
                ALTER TABLE bunker_room_setups ADD COLUMN IF NOT EXISTS guild_id BIGINT;
                ALTER TABLE bunker_room_setups ADD COLUMN IF NOT EXISTS setup_channel_id BIGINT;
                ALTER TABLE bunker_room_setups ADD COLUMN IF NOT EXISTS category_id BIGINT;
                ALTER TABLE bunker_room_setups ADD COLUMN IF NOT EXISTS setup_message_id BIGINT;
                ALTER TABLE bunker_room_setups ADD COLUMN IF NOT EXISTS room_name TEXT;
                ALTER TABLE bunker_room_setups ADD COLUMN IF NOT EXISTS active_game_id BIGINT;
                ALTER TABLE bunker_room_setups ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
                ALTER TABLE bunker_room_setups ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS setup_message_id BIGINT;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS category_id BIGINT;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS game_text_channel_id BIGINT;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS voice_channel_id BIGINT;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS board_message_id BIGINT;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS settings JSONB NOT NULL DEFAULT '{}';
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS bunker_profile JSONB;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS recent_events JSONB NOT NULL DEFAULT '[]';
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS is_admin_game BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;

                ALTER TABLE bunker_players ADD COLUMN IF NOT EXISTS ready_at TIMESTAMPTZ;
                ALTER TABLE bunker_players ADD COLUMN IF NOT EXISTS invited_at TIMESTAMPTZ;
                ALTER TABLE bunker_players ADD COLUMN IF NOT EXISTS joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
                ALTER TABLE bunker_players ADD COLUMN IF NOT EXISTS left_at TIMESTAMPTZ;
                ALTER TABLE bunker_players ADD COLUMN IF NOT EXISTS is_eliminated BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE bunker_players ADD COLUMN IF NOT EXISTS card JSONB;
                ALTER TABLE bunker_players ADD COLUMN IF NOT EXISTS revealed_stats JSONB NOT NULL DEFAULT '[]';
                ALTER TABLE bunker_players ADD COLUMN IF NOT EXISTS used_special_action BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE bunker_players ADD COLUMN IF NOT EXISTS immune_round INTEGER;
                ALTER TABLE bunker_players ADD COLUMN IF NOT EXISTS personal_bonus INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE bunker_players ADD COLUMN IF NOT EXISTS is_fake BOOLEAN NOT NULL DEFAULT FALSE;

                CREATE UNIQUE INDEX IF NOT EXISTS idx_bunker_room_setups_setup_channel_id
                    ON bunker_room_setups (setup_channel_id);
                """
            )

    async def get_or_create_guild_settings(self, guild_id: int) -> BunkerGuildSettings:
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_guild_settings (guild_id)
            VALUES ($1)
            ON CONFLICT (guild_id) DO UPDATE SET updated_at = bunker_guild_settings.updated_at
            RETURNING *
            """,
            guild_id,
        )
        return _guild_settings_from_row(row)

    async def set_operator_role(self, guild_id: int, role_id: int | None) -> BunkerGuildSettings:
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_guild_settings (guild_id, operator_role_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET operator_role_id = EXCLUDED.operator_role_id, updated_at = NOW()
            RETURNING *
            """,
            guild_id,
            role_id,
        )
        return _guild_settings_from_row(row)

    async def is_bunker_operator(self, guild_id: int, role_ids: list[int]) -> bool:
        settings = await self.get_or_create_guild_settings(guild_id)
        return settings.operator_role_id is not None and settings.operator_role_id in set(role_ids)

    async def upsert_room_setup(
        self,
        *,
        guild_id: int,
        setup_channel_id: int,
        category_id: int | None,
        setup_message_id: int,
        room_name: str,
    ) -> RoomSetup:
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_room_setups (guild_id, setup_channel_id, category_id, setup_message_id, room_name)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (setup_channel_id)
            DO UPDATE SET
                category_id = EXCLUDED.category_id,
                setup_message_id = EXCLUDED.setup_message_id,
                room_name = EXCLUDED.room_name,
                updated_at = NOW()
            RETURNING *
            """,
            guild_id,
            setup_channel_id,
            category_id,
            setup_message_id,
            room_name,
        )
        return _setup_from_row(row)

    async def get_setup_by_message(self, message_id: int) -> RoomSetup | None:
        row = await self.pool.fetchrow("SELECT * FROM bunker_room_setups WHERE setup_message_id = $1", message_id)
        return _setup_from_row(row) if row else None

    async def get_setup_by_channel(self, channel_id: int) -> RoomSetup | None:
        row = await self.pool.fetchrow("SELECT * FROM bunker_room_setups WHERE setup_channel_id = $1", channel_id)
        return _setup_from_row(row) if row else None

    async def get_draft(self, setup_id: int, user_id: int) -> BunkerSettings:
        row = await self.pool.fetchrow(
            """
            SELECT settings
            FROM bunker_host_drafts
            WHERE setup_id = $1 AND user_id = $2
            """,
            setup_id,
            user_id,
        )
        return BunkerSettings.from_json(_json_load(row["settings"]) if row else None)

    async def save_draft(self, setup_id: int, user_id: int, settings: BunkerSettings) -> None:
        await self.pool.execute(
            """
            INSERT INTO bunker_host_drafts (setup_id, user_id, settings)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (setup_id, user_id)
            DO UPDATE SET settings = EXCLUDED.settings, updated_at = NOW()
            """,
            setup_id,
            user_id,
            json.dumps(settings.to_json(), ensure_ascii=False),
        )

    async def create_game(
        self,
        *,
        setup: RoomSetup,
        host_id: int,
        settings: BunkerSettings,
        text_channel_id: int,
        voice_channel_id: int,
        host_display_name: str,
        is_admin_game: bool = False,
    ) -> BunkerGame:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                active = await connection.fetchrow(
                    "SELECT id FROM bunker_games WHERE setup_id = $1 AND finished_at IS NULL",
                    setup.id,
                )
                if active is not None:
                    raise ActiveBunkerGameError(int(active["id"]))

                row = await connection.fetchrow(
                    """
                    INSERT INTO bunker_games (
                        guild_id, setup_id, setup_channel_id, setup_message_id, category_id,
                        game_text_channel_id, voice_channel_id, host_id, state, mode, is_public,
                        slots, max_rounds, timer_seconds, settings, is_admin_game
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'lobby', $9, $10, $11, $12, $13, $14::jsonb, $15)
                    RETURNING *
                    """,
                    setup.guild_id,
                    setup.id,
                    setup.setup_channel_id,
                    setup.setup_message_id,
                    setup.category_id,
                    text_channel_id,
                    voice_channel_id,
                    host_id,
                    settings.mode.value,
                    settings.is_public,
                    settings.slots,
                    settings.rounds,
                    settings.timer_seconds,
                    json.dumps(settings.to_json(), ensure_ascii=False),
                    is_admin_game,
                )
                await connection.execute(
                    """
                    INSERT INTO bunker_players (game_id, user_id, display_name, is_host)
                    VALUES ($1, $2, $3, TRUE)
                    ON CONFLICT (game_id, user_id)
                    DO UPDATE SET display_name = EXCLUDED.display_name, is_host = TRUE, left_at = NULL
                    """,
                    int(row["id"]),
                    host_id,
                    host_display_name,
                )
                await connection.execute(
                    """
                    UPDATE bunker_room_setups
                    SET active_game_id = $2, updated_at = NOW()
                    WHERE id = $1
                    """,
                    setup.id,
                    int(row["id"]),
                )

        return _game_from_row(row)

    async def get_active_game_by_setup(self, setup_id: int) -> BunkerGame | None:
        row = await self.pool.fetchrow(
            """
            SELECT *
            FROM bunker_games
            WHERE setup_id = $1 AND finished_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            setup_id,
        )
        return _game_from_row(row) if row else None

    async def get_active_game_by_text_channel(self, channel_id: int) -> BunkerGame | None:
        row = await self.pool.fetchrow(
            """
            SELECT *
            FROM bunker_games
            WHERE game_text_channel_id = $1 AND finished_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            channel_id,
        )
        return _game_from_row(row) if row else None

    async def get_game(self, game_id: int) -> BunkerGame | None:
        row = await self.pool.fetchrow("SELECT * FROM bunker_games WHERE id = $1", game_id)
        return _game_from_row(row) if row else None

    async def set_board_message(self, game_id: int, message_id: int) -> None:
        await self.pool.execute(
            "UPDATE bunker_games SET board_message_id = $2, updated_at = NOW() WHERE id = $1",
            game_id,
            message_id,
        )

    async def set_game_state(
        self,
        game_id: int,
        state: GameState,
        *,
        round_number: int | None = None,
        phase_started_at: datetime | None = None,
        phase_ends_at: datetime | None = None,
        paused_at: datetime | None = None,
    ) -> BunkerGame:
        row = await self.pool.fetchrow(
            """
            UPDATE bunker_games
            SET state = $2,
                round_number = COALESCE($3, round_number),
                phase_started_at = $4,
                phase_ends_at = $5,
                paused_at = $6,
                updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            game_id,
            state.value,
            round_number,
            phase_started_at,
            phase_ends_at,
            paused_at,
        )
        return _game_from_row(row)

    async def set_profile(self, game_id: int, profile: BunkerProfile) -> None:
        await self.pool.execute(
            """
            UPDATE bunker_games
            SET bunker_profile = $2::jsonb, updated_at = NOW()
            WHERE id = $1
            """,
            game_id,
            json.dumps(profile.to_json(), ensure_ascii=False),
        )

    async def set_resources(self, game_id: int, profile: BunkerProfile) -> None:
        await self.set_profile(game_id, profile)

    async def add_event(self, game_id: int, round_number: int, event_type: str, body: str) -> None:
        await self.pool.execute(
            """
            INSERT INTO bunker_game_events (game_id, round_number, event_type, body)
            VALUES ($1, $2, $3, $4)
            """,
            game_id,
            round_number,
            event_type,
            body,
        )
        rows = await self.pool.fetch(
            """
            SELECT body
            FROM bunker_game_events
            WHERE game_id = $1
            ORDER BY id DESC
            LIMIT 6
            """,
            game_id,
        )
        events = [str(row["body"]) for row in reversed(rows)]
        await self.pool.execute(
            """
            UPDATE bunker_games
            SET recent_events = $2::jsonb, updated_at = NOW()
            WHERE id = $1
            """,
            game_id,
            json.dumps(events, ensure_ascii=False),
        )

    async def add_or_restore_player(self, game_id: int, user_id: int, display_name: str, *, is_fake: bool = False) -> BunkerPlayer:
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_players (game_id, user_id, display_name, is_fake)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (game_id, user_id)
            DO UPDATE SET display_name = EXCLUDED.display_name, left_at = NULL, is_fake = EXCLUDED.is_fake
            RETURNING *
            """,
            game_id,
            user_id,
            display_name,
            is_fake,
        )
        return _player_from_row(row)

    async def add_fake_players(self, game_id: int, total_player_target: int) -> list[BunkerPlayer]:
        existing = await self.list_players(game_id)
        missing = max(0, total_player_target - len(existing))
        if missing <= 0:
            return []

        existing_fake_count = sum(1 for player in existing if player.is_fake)
        rows: list[asyncpg.Record] = []
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                for offset in range(1, missing + 1):
                    number = existing_fake_count + offset
                    user_id = -((game_id * 1000) + number)
                    row = await connection.fetchrow(
                        """
                        INSERT INTO bunker_players (game_id, user_id, display_name, ready_at, is_fake)
                        VALUES ($1, $2, $3, NOW(), TRUE)
                        ON CONFLICT (game_id, user_id)
                        DO UPDATE SET display_name = EXCLUDED.display_name,
                                      ready_at = NOW(),
                                      left_at = NULL,
                                      is_fake = TRUE
                        RETURNING *
                        """,
                        game_id,
                        user_id,
                        f"Тестовый выживший {number}",
                    )
                    rows.append(row)

        return [_player_from_row(row) for row in rows]

    async def remove_fake_players(self, game_id: int) -> int:
        result = await self.pool.execute(
            """
            DELETE FROM bunker_players
            WHERE game_id = $1 AND is_fake = TRUE
            """,
            game_id,
        )
        return int(result.rsplit(" ", 1)[-1])

    async def list_fake_players(self, game_id: int) -> list[BunkerPlayer]:
        rows = await self.pool.fetch(
            """
            SELECT *
            FROM bunker_players
            WHERE game_id = $1 AND is_fake = TRUE
            ORDER BY joined_at ASC, user_id DESC
            """,
            game_id,
        )
        return [_player_from_row(row) for row in rows]

    async def list_players(self, game_id: int, *, include_left: bool = False) -> list[BunkerPlayer]:
        condition = "" if include_left else "AND left_at IS NULL"
        rows = await self.pool.fetch(
            f"""
            SELECT *
            FROM bunker_players
            WHERE game_id = $1 {condition}
            ORDER BY is_host DESC, joined_at ASC, user_id ASC
            """,
            game_id,
        )
        return [_player_from_row(row) for row in rows]

    async def get_player(self, game_id: int, user_id: int) -> BunkerPlayer | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM bunker_players WHERE game_id = $1 AND user_id = $2",
            game_id,
            user_id,
        )
        return _player_from_row(row) if row else None

    async def set_ready(self, game_id: int, user_id: int, ready: bool = True) -> None:
        await self.pool.execute(
            """
            UPDATE bunker_players
            SET ready_at = CASE WHEN $3 THEN NOW() ELSE NULL END
            WHERE game_id = $1 AND user_id = $2 AND left_at IS NULL
            """,
            game_id,
            user_id,
            ready,
        )

    async def mark_left(self, game_id: int, user_id: int) -> None:
        await self.pool.execute(
            """
            UPDATE bunker_players
            SET left_at = NOW(), ready_at = NULL
            WHERE game_id = $1 AND user_id = $2 AND is_host = FALSE
            """,
            game_id,
            user_id,
        )

    async def assign_cards(self, game_id: int, cards: dict[int, CharacterCard]) -> None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                for user_id, card in cards.items():
                    await connection.execute(
                        """
                        UPDATE bunker_players
                        SET card = $3::jsonb
                        WHERE game_id = $1 AND user_id = $2
                        """,
                        game_id,
                        user_id,
                        json.dumps(card.to_json(), ensure_ascii=False),
                    )

    async def reveal_stat(self, game_id: int, user_id: int, stat: str) -> None:
        player = await self.get_player(game_id, user_id)
        if player is None:
            return

        revealed = list(player.revealed_stats)
        if stat not in revealed:
            revealed.append(stat)
        await self.pool.execute(
            """
            UPDATE bunker_players
            SET revealed_stats = $3::jsonb
            WHERE game_id = $1 AND user_id = $2
            """,
            game_id,
            user_id,
            json.dumps(revealed, ensure_ascii=False),
        )

    async def mark_special_used(self, game_id: int, user_id: int) -> None:
        await self.pool.execute(
            """
            UPDATE bunker_players
            SET used_special_action = TRUE
            WHERE game_id = $1 AND user_id = $2
            """,
            game_id,
            user_id,
        )

    async def mark_eliminated(self, game_id: int, user_id: int) -> None:
        await self.pool.execute(
            """
            UPDATE bunker_players
            SET is_eliminated = TRUE
            WHERE game_id = $1 AND user_id = $2
            """,
            game_id,
            user_id,
        )

    async def save_vote(self, vote: Vote) -> None:
        await self.pool.execute(
            """
            INSERT INTO bunker_votes (game_id, round_number, voter_id, target_user_id, is_abstain)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (game_id, round_number, voter_id)
            DO UPDATE SET target_user_id = EXCLUDED.target_user_id,
                          is_abstain = EXCLUDED.is_abstain,
                          changed_at = NOW()
            """,
            vote.game_id,
            vote.round_number,
            vote.voter_id,
            vote.target_user_id,
            vote.is_abstain,
        )

    async def list_votes(self, game_id: int, round_number: int) -> list[Vote]:
        rows = await self.pool.fetch(
            """
            SELECT *
            FROM bunker_votes
            WHERE game_id = $1 AND round_number = $2
            """,
            game_id,
            round_number,
        )
        return [_vote_from_row(row) for row in rows]

    async def list_due_games(self, now: datetime | None = None) -> list[BunkerGame]:
        now = now or datetime.now(UTC)
        rows = await self.pool.fetch(
            """
            SELECT *
            FROM bunker_games
            WHERE finished_at IS NULL
              AND paused_at IS NULL
              AND phase_ends_at IS NOT NULL
              AND phase_ends_at <= $1
            ORDER BY phase_ends_at ASC
            """,
            now,
        )
        return [_game_from_row(row) for row in rows]

    async def finish_game(self, game_id: int) -> None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow("SELECT setup_id FROM bunker_games WHERE id = $1", game_id)
                await connection.execute(
                    """
                    UPDATE bunker_games
                    SET state = 'finished', finished_at = NOW(), phase_ends_at = NULL, updated_at = NOW()
                    WHERE id = $1
                    """,
                    game_id,
                )
                if row is not None:
                    await connection.execute(
                        """
                        UPDATE bunker_room_setups
                        SET active_game_id = NULL, updated_at = NOW()
                        WHERE id = $1 AND active_game_id = $2
                        """,
                        int(row["setup_id"]),
                        game_id,
                    )


class ActiveBunkerGameError(RuntimeError):
    def __init__(self, game_id: int) -> None:
        super().__init__(f"Active bunker game already exists: {game_id}")
        self.game_id = game_id


def _setup_from_row(row: asyncpg.Record) -> RoomSetup:
    return RoomSetup(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        setup_channel_id=int(row["setup_channel_id"]),
        category_id=int(row["category_id"]) if row["category_id"] is not None else None,
        setup_message_id=int(row["setup_message_id"]) if row["setup_message_id"] is not None else None,
        room_name=str(row["room_name"]),
        active_game_id=int(row["active_game_id"]) if row["active_game_id"] is not None else None,
    )


def _guild_settings_from_row(row: asyncpg.Record) -> BunkerGuildSettings:
    return BunkerGuildSettings(
        guild_id=int(row["guild_id"]),
        operator_role_id=int(row["operator_role_id"]) if row["operator_role_id"] is not None else None,
    )


def _game_from_row(row: asyncpg.Record) -> BunkerGame:
    settings = BunkerSettings.from_json(_json_load(row["settings"]))
    return BunkerGame(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        setup_id=int(row["setup_id"]),
        setup_channel_id=int(row["setup_channel_id"]),
        setup_message_id=int(row["setup_message_id"]) if row["setup_message_id"] is not None else None,
        category_id=int(row["category_id"]) if row["category_id"] is not None else None,
        game_text_channel_id=int(row["game_text_channel_id"]) if row["game_text_channel_id"] is not None else None,
        voice_channel_id=int(row["voice_channel_id"]) if row["voice_channel_id"] is not None else None,
        host_id=int(row["host_id"]),
        state=GameState(str(row["state"])),
        settings=settings,
        round_number=int(row["round_number"]),
        phase_started_at=row["phase_started_at"],
        phase_ends_at=row["phase_ends_at"],
        paused_at=row["paused_at"],
        board_message_id=int(row["board_message_id"]) if row["board_message_id"] is not None else None,
        profile=BunkerProfile.from_json(_json_load(row["bunker_profile"])),
        is_admin_game=bool(row["is_admin_game"]),
        recent_events=tuple(str(event) for event in _json_load(row["recent_events"], [])),
        finished_at=row["finished_at"],
    )


def _player_from_row(row: asyncpg.Record) -> BunkerPlayer:
    return BunkerPlayer(
        game_id=int(row["game_id"]),
        user_id=int(row["user_id"]),
        display_name=str(row["display_name"]),
        is_host=bool(row["is_host"]),
        ready_at=row["ready_at"],
        invited_at=row["invited_at"],
        joined_at=row["joined_at"],
        left_at=row["left_at"],
        is_eliminated=bool(row["is_eliminated"]),
        card=CharacterCard.from_json(_json_load(row["card"])),
        revealed_stats=tuple(str(stat) for stat in _json_load(row["revealed_stats"], [])),
        used_special_action=bool(row["used_special_action"]),
        immune_round=int(row["immune_round"]) if row["immune_round"] is not None else None,
        personal_bonus=int(row["personal_bonus"]),
        is_fake=bool(row["is_fake"]),
    )


def _vote_from_row(row: asyncpg.Record) -> Vote:
    return Vote(
        game_id=int(row["game_id"]),
        round_number=int(row["round_number"]),
        voter_id=int(row["voter_id"]),
        target_user_id=int(row["target_user_id"]) if row["target_user_id"] is not None else None,
        is_abstain=bool(row["is_abstain"]),
    )


def _json_load(raw: Any, default: Any = None) -> Any:
    if raw is None:
        return default
    if isinstance(raw, str):
        return json.loads(raw)
    return raw
