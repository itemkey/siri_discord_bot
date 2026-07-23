from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import asyncpg

from siri_bot.bunker.content import PACK_FIELDS, normalize_pack_content
from siri_bot.bunker.models import (
    BunkerBuilderProgress,
    BunkerContentPack,
    BunkerGame,
    BunkerGuildSettings,
    BunkerPackSubmission,
    BunkerPlayer,
    BunkerProfile,
    BunkerSettings,
    CharacterCard,
    GameState,
    PackSubmissionStatus,
    REVEALABLE_STATS,
    RoomKind,
    RoomStatus,
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
                    interest_role_id BIGINT,
                    builder_reward_role_id BIGINT,
                    builder_info_channel_id BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS bunker_content_packs (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    content JSONB NOT NULL DEFAULT '{}',
                    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    created_by BIGINT NOT NULL,
                    updated_by BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS bunker_builder_progress (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    agreement_version INTEGER NOT NULL DEFAULT 1,
                    accepted_agreement_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS bunker_pack_submissions (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    author_id BIGINT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    content JSONB NOT NULL DEFAULT '{}',
                    source_filename TEXT NOT NULL DEFAULT '',
                    reviewer_id BIGINT,
                    content_pack_id BIGINT REFERENCES bunker_content_packs(id) ON DELETE SET NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    reviewed_at TIMESTAMPTZ
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
                    room_kind TEXT NOT NULL DEFAULT 'ranked',
                    room_index INTEGER NOT NULL DEFAULT 0,
                    room_status TEXT NOT NULL DEFAULT 'lobby',
                    public_message_ids JSONB NOT NULL DEFAULT '{}',
                    turn_order JSONB NOT NULL DEFAULT '[]',
                    current_turn_index INTEGER NOT NULL DEFAULT 0,
                    reveals_done_this_turn INTEGER NOT NULL DEFAULT 0,
                    speech_index INTEGER NOT NULL DEFAULT 0,
                    collapsed_sections JSONB NOT NULL DEFAULT '{}',
                    finished_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

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
                    final_revealed BOOLEAN NOT NULL DEFAULT FALSE,
                    PRIMARY KEY (game_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS bunker_votes (
                    game_id BIGINT NOT NULL REFERENCES bunker_games(id) ON DELETE CASCADE,
                    round_number INTEGER NOT NULL,
                    voter_id BIGINT NOT NULL,
                    target_user_id BIGINT,
                    is_abstain BOOLEAN NOT NULL DEFAULT FALSE,
                    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    confirmed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
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

                CREATE TABLE IF NOT EXISTS bunker_xp_awards (
                    game_id BIGINT NOT NULL REFERENCES bunker_games(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    amount INTEGER NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (game_id, user_id)
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

                ALTER TABLE bunker_guild_settings ADD COLUMN IF NOT EXISTS interest_role_id BIGINT;
                ALTER TABLE bunker_guild_settings ADD COLUMN IF NOT EXISTS builder_reward_role_id BIGINT;
                ALTER TABLE bunker_guild_settings ADD COLUMN IF NOT EXISTS builder_info_channel_id BIGINT;

                ALTER TABLE bunker_content_packs ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';
                ALTER TABLE bunker_content_packs ADD COLUMN IF NOT EXISTS content JSONB NOT NULL DEFAULT '{}';
                ALTER TABLE bunker_content_packs ADD COLUMN IF NOT EXISTS is_enabled BOOLEAN NOT NULL DEFAULT TRUE;
                ALTER TABLE bunker_content_packs ADD COLUMN IF NOT EXISTS updated_by BIGINT;
                ALTER TABLE bunker_content_packs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
                ALTER TABLE bunker_content_packs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

                ALTER TABLE bunker_builder_progress ADD COLUMN IF NOT EXISTS agreement_version INTEGER NOT NULL DEFAULT 1;
                ALTER TABLE bunker_builder_progress ADD COLUMN IF NOT EXISTS accepted_agreement_at TIMESTAMPTZ;
                ALTER TABLE bunker_builder_progress ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
                ALTER TABLE bunker_builder_progress ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
                ALTER TABLE bunker_builder_progress ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

                ALTER TABLE bunker_pack_submissions ADD COLUMN IF NOT EXISTS guild_id BIGINT;
                ALTER TABLE bunker_pack_submissions ADD COLUMN IF NOT EXISTS author_id BIGINT;
                ALTER TABLE bunker_pack_submissions ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
                ALTER TABLE bunker_pack_submissions ADD COLUMN IF NOT EXISTS name TEXT NOT NULL DEFAULT 'Новый пак';
                ALTER TABLE bunker_pack_submissions ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT '';
                ALTER TABLE bunker_pack_submissions ADD COLUMN IF NOT EXISTS content JSONB NOT NULL DEFAULT '{}';
                ALTER TABLE bunker_pack_submissions ADD COLUMN IF NOT EXISTS source_filename TEXT NOT NULL DEFAULT '';
                ALTER TABLE bunker_pack_submissions ADD COLUMN IF NOT EXISTS reviewer_id BIGINT;
                ALTER TABLE bunker_pack_submissions ADD COLUMN IF NOT EXISTS content_pack_id BIGINT;
                ALTER TABLE bunker_pack_submissions ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
                ALTER TABLE bunker_pack_submissions ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;

                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS setup_message_id BIGINT;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS category_id BIGINT;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS game_text_channel_id BIGINT;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS voice_channel_id BIGINT;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS board_message_id BIGINT;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS settings JSONB NOT NULL DEFAULT '{}';
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS bunker_profile JSONB;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS recent_events JSONB NOT NULL DEFAULT '[]';
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS is_admin_game BOOLEAN NOT NULL DEFAULT FALSE;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS room_kind TEXT NOT NULL DEFAULT 'ranked';
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS room_index INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS room_status TEXT NOT NULL DEFAULT 'lobby';
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS public_message_ids JSONB NOT NULL DEFAULT '{}';
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS turn_order JSONB NOT NULL DEFAULT '[]';
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS current_turn_index INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS reveals_done_this_turn INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS speech_index INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS collapsed_sections JSONB NOT NULL DEFAULT '{}';
                ALTER TABLE bunker_games ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ;

                UPDATE bunker_games
                SET room_kind = CASE
                    WHEN is_admin_game THEN 'admin_test'
                    ELSE 'ranked'
                END
                WHERE NOT (settings ? 'room_kind');

                UPDATE bunker_games
                SET room_kind = 'ranked'
                WHERE room_kind = 'casual' AND NOT is_admin_game;

                UPDATE bunker_games
                SET phase_ends_at = NULL
                WHERE state = 'reveal_phase' AND room_status = 'active';

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
                ALTER TABLE bunker_players ADD COLUMN IF NOT EXISTS final_revealed BOOLEAN NOT NULL DEFAULT FALSE;

                ALTER TABLE bunker_votes ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

                CREATE UNIQUE INDEX IF NOT EXISTS idx_bunker_room_setups_setup_channel_id
                    ON bunker_room_setups (setup_channel_id);
                CREATE INDEX IF NOT EXISTS idx_bunker_content_packs_guild_id
                    ON bunker_content_packs (guild_id);
                CREATE INDEX IF NOT EXISTS idx_bunker_pack_submissions_guild_status
                    ON bunker_pack_submissions (guild_id, status, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_bunker_pack_submissions_author
                    ON bunker_pack_submissions (guild_id, author_id, created_at DESC);
                DROP INDEX IF EXISTS idx_bunker_active_game_per_setup;
                DROP INDEX IF EXISTS idx_bunker_active_game_by_host;
                DROP INDEX IF EXISTS idx_bunker_game_text_channel;
                CREATE INDEX IF NOT EXISTS idx_bunker_active_game_by_host
                    ON bunker_games (guild_id, host_id)
                    WHERE room_status IN ('lobby', 'active') AND finished_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_bunker_game_text_channel
                    ON bunker_games (game_text_channel_id)
                    WHERE room_status IN ('lobby', 'active') AND finished_at IS NULL;
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

    async def set_interest_role(self, guild_id: int, role_id: int | None) -> BunkerGuildSettings:
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_guild_settings (guild_id, interest_role_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET interest_role_id = EXCLUDED.interest_role_id, updated_at = NOW()
            RETURNING *
            """,
            guild_id,
            role_id,
        )
        return _guild_settings_from_row(row)

    async def set_builder_reward_role(self, guild_id: int, role_id: int | None) -> BunkerGuildSettings:
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_guild_settings (guild_id, builder_reward_role_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET builder_reward_role_id = EXCLUDED.builder_reward_role_id, updated_at = NOW()
            RETURNING *
            """,
            guild_id,
            role_id,
        )
        return _guild_settings_from_row(row)

    async def set_builder_info_channel(self, guild_id: int, channel_id: int | None) -> BunkerGuildSettings:
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_guild_settings (guild_id, builder_info_channel_id)
            VALUES ($1, $2)
            ON CONFLICT (guild_id)
            DO UPDATE SET builder_info_channel_id = EXCLUDED.builder_info_channel_id, updated_at = NOW()
            RETURNING *
            """,
            guild_id,
            channel_id,
        )
        return _guild_settings_from_row(row)

    async def is_bunker_operator(self, guild_id: int, role_ids: list[int]) -> bool:
        settings = await self.get_or_create_guild_settings(guild_id)
        return settings.operator_role_id is not None and settings.operator_role_id in set(role_ids)

    async def get_builder_progress(
        self,
        guild_id: int,
        user_id: int,
        *,
        agreement_version: int = 1,
    ) -> BunkerBuilderProgress:
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_builder_progress (guild_id, user_id, agreement_version)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, user_id) DO UPDATE
            SET agreement_version = GREATEST(bunker_builder_progress.agreement_version, EXCLUDED.agreement_version),
                updated_at = bunker_builder_progress.updated_at
            RETURNING *
            """,
            guild_id,
            user_id,
            agreement_version,
        )
        return _builder_progress_from_row(row)

    async def accept_builder_agreement(
        self,
        guild_id: int,
        user_id: int,
        *,
        agreement_version: int = 1,
    ) -> BunkerBuilderProgress:
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_builder_progress (
                guild_id,
                user_id,
                agreement_version,
                accepted_agreement_at
            )
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (guild_id, user_id) DO UPDATE
            SET agreement_version = EXCLUDED.agreement_version,
                accepted_agreement_at = COALESCE(bunker_builder_progress.accepted_agreement_at, NOW()),
                updated_at = NOW()
            RETURNING *
            """,
            guild_id,
            user_id,
            agreement_version,
        )
        return _builder_progress_from_row(row)

    async def complete_builder_tutorial(
        self,
        guild_id: int,
        user_id: int,
        *,
        agreement_version: int = 1,
    ) -> BunkerBuilderProgress:
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_builder_progress (
                guild_id,
                user_id,
                agreement_version,
                accepted_agreement_at,
                completed_at
            )
            VALUES ($1, $2, $3, NOW(), NOW())
            ON CONFLICT (guild_id, user_id) DO UPDATE
            SET agreement_version = EXCLUDED.agreement_version,
                accepted_agreement_at = COALESCE(bunker_builder_progress.accepted_agreement_at, NOW()),
                completed_at = COALESCE(bunker_builder_progress.completed_at, NOW()),
                updated_at = NOW()
            RETURNING *
            """,
            guild_id,
            user_id,
            agreement_version,
        )
        return _builder_progress_from_row(row)

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

    async def repair_setup_message_id(self, setup_id: int, message_id: int) -> RoomSetup | None:
        row = await self.pool.fetchrow(
            """
            UPDATE bunker_room_setups
            SET setup_message_id = $2, updated_at = NOW()
            WHERE id = $1
            RETURNING *
            """,
            setup_id,
            message_id,
        )
        return _setup_from_row(row) if row else None

    async def create_content_pack(
        self,
        *,
        guild_id: int,
        name: str,
        created_by: int,
        description: str = "",
        content: dict[str, tuple[str, ...]] | None = None,
    ) -> BunkerContentPack:
        normalized = normalize_pack_content(content or {})
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_content_packs (guild_id, name, description, content, created_by, updated_by)
            VALUES ($1, $2, $3, $4::jsonb, $5, $5)
            RETURNING *
            """,
            guild_id,
            name.strip()[:80] or "Новый пак",
            description.strip()[:500],
            json.dumps(_pack_content_to_json(normalized), ensure_ascii=False),
            created_by,
        )
        return _content_pack_from_row(row)

    async def list_content_packs(self, guild_id: int, *, include_disabled: bool = True) -> list[BunkerContentPack]:
        condition = "" if include_disabled else "AND is_enabled = TRUE"
        rows = await self.pool.fetch(
            f"""
            SELECT *
            FROM bunker_content_packs
            WHERE guild_id = $1 {condition}
            ORDER BY is_enabled DESC, updated_at DESC, id DESC
            """,
            guild_id,
        )
        return [_content_pack_from_row(row) for row in rows]

    async def get_content_pack(self, pack_id: int, *, guild_id: int | None = None) -> BunkerContentPack | None:
        if guild_id is None:
            row = await self.pool.fetchrow("SELECT * FROM bunker_content_packs WHERE id = $1", pack_id)
        else:
            row = await self.pool.fetchrow("SELECT * FROM bunker_content_packs WHERE id = $1 AND guild_id = $2", pack_id, guild_id)
        return _content_pack_from_row(row) if row else None

    async def get_enabled_content_pack(self, guild_id: int, pack_id: int | None) -> BunkerContentPack | None:
        if pack_id is None:
            return None
        row = await self.pool.fetchrow(
            """
            SELECT *
            FROM bunker_content_packs
            WHERE id = $1 AND guild_id = $2 AND is_enabled = TRUE
            """,
            pack_id,
            guild_id,
        )
        return _content_pack_from_row(row) if row else None

    async def update_content_pack(
        self,
        pack_id: int,
        *,
        guild_id: int,
        updated_by: int,
        name: str | None = None,
        description: str | None = None,
        content: dict[str, tuple[str, ...]] | None = None,
        is_enabled: bool | None = None,
    ) -> BunkerContentPack | None:
        current = await self.get_content_pack(pack_id, guild_id=guild_id)
        if current is None:
            return None
        next_content = current.content if content is None else normalize_pack_content(content)
        row = await self.pool.fetchrow(
            """
            UPDATE bunker_content_packs
            SET name = $3,
                description = $4,
                content = $5::jsonb,
                is_enabled = $6,
                updated_by = $7,
                updated_at = NOW()
            WHERE id = $1 AND guild_id = $2
            RETURNING *
            """,
            pack_id,
            guild_id,
            (name.strip()[:80] if name is not None else current.name) or "Новый пак",
            description.strip()[:500] if description is not None else current.description,
            json.dumps(_pack_content_to_json(next_content), ensure_ascii=False),
            current.is_enabled if is_enabled is None else is_enabled,
            updated_by,
        )
        return _content_pack_from_row(row) if row else None

    async def delete_content_pack(self, pack_id: int, *, guild_id: int) -> bool:
        result = await self.pool.execute(
            "DELETE FROM bunker_content_packs WHERE id = $1 AND guild_id = $2",
            pack_id,
            guild_id,
        )
        return result.endswith(" 1")

    async def create_pack_submission(
        self,
        *,
        guild_id: int,
        author_id: int,
        name: str,
        description: str = "",
        content: dict[str, tuple[str, ...]],
        source_filename: str = "",
    ) -> BunkerPackSubmission:
        normalized = normalize_pack_content(content)
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_pack_submissions (
                guild_id,
                author_id,
                status,
                name,
                description,
                content,
                source_filename
            )
            VALUES ($1, $2, 'pending', $3, $4, $5::jsonb, $6)
            RETURNING *
            """,
            guild_id,
            author_id,
            name.strip()[:80] or "Новый пак",
            description.strip()[:500],
            json.dumps(_pack_content_to_json(normalized), ensure_ascii=False),
            source_filename.strip()[:120],
        )
        return _pack_submission_from_row(row)

    async def list_pack_submissions(
        self,
        guild_id: int,
        *,
        status: PackSubmissionStatus | str = PackSubmissionStatus.PENDING,
        limit: int = 20,
        offset: int = 0,
    ) -> list[BunkerPackSubmission]:
        rows = await self.pool.fetch(
            """
            SELECT *
            FROM bunker_pack_submissions
            WHERE guild_id = $1 AND status = $2
            ORDER BY created_at ASC, id ASC
            LIMIT $3 OFFSET $4
            """,
            guild_id,
            str(status),
            max(1, min(limit, 100)),
            max(0, offset),
        )
        return [_pack_submission_from_row(row) for row in rows]

    async def count_pack_submissions(
        self,
        guild_id: int,
        *,
        status: PackSubmissionStatus | str = PackSubmissionStatus.PENDING,
    ) -> int:
        return int(
            await self.pool.fetchval(
                """
                SELECT COUNT(*)
                FROM bunker_pack_submissions
                WHERE guild_id = $1 AND status = $2
                """,
                guild_id,
                str(status),
            )
        )

    async def get_pack_submission(self, submission_id: int, *, guild_id: int | None = None) -> BunkerPackSubmission | None:
        if guild_id is None:
            row = await self.pool.fetchrow("SELECT * FROM bunker_pack_submissions WHERE id = $1", submission_id)
        else:
            row = await self.pool.fetchrow(
                "SELECT * FROM bunker_pack_submissions WHERE id = $1 AND guild_id = $2",
                submission_id,
                guild_id,
            )
        return _pack_submission_from_row(row) if row else None

    async def accept_pack_submission(
        self,
        submission_id: int,
        *,
        guild_id: int,
        reviewer_id: int,
    ) -> tuple[BunkerPackSubmission, BunkerContentPack] | None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                submission_row = await connection.fetchrow(
                    """
                    SELECT *
                    FROM bunker_pack_submissions
                    WHERE id = $1 AND guild_id = $2 AND status = 'pending'
                    FOR UPDATE
                    """,
                    submission_id,
                    guild_id,
                )
                if submission_row is None:
                    return None
                submission = _pack_submission_from_row(submission_row)
                pack_row = await connection.fetchrow(
                    """
                    INSERT INTO bunker_content_packs (
                        guild_id,
                        name,
                        description,
                        content,
                        created_by,
                        updated_by
                    )
                    VALUES ($1, $2, $3, $4::jsonb, $5, $6)
                    RETURNING *
                    """,
                    guild_id,
                    submission.name.strip()[:80] or "Новый пак",
                    submission.description.strip()[:500],
                    json.dumps(_pack_content_to_json(submission.content), ensure_ascii=False),
                    submission.author_id,
                    reviewer_id,
                )
                updated_row = await connection.fetchrow(
                    """
                    UPDATE bunker_pack_submissions
                    SET status = 'accepted',
                        reviewer_id = $3,
                        content_pack_id = $4,
                        reviewed_at = NOW()
                    WHERE id = $1 AND guild_id = $2
                    RETURNING *
                    """,
                    submission_id,
                    guild_id,
                    reviewer_id,
                    int(pack_row["id"]),
                )
        return _pack_submission_from_row(updated_row), _content_pack_from_row(pack_row)

    async def reject_pack_submission(
        self,
        submission_id: int,
        *,
        guild_id: int,
        reviewer_id: int,
    ) -> BunkerPackSubmission | None:
        row = await self.pool.fetchrow(
            """
            UPDATE bunker_pack_submissions
            SET status = 'rejected',
                reviewer_id = $3,
                reviewed_at = NOW()
            WHERE id = $1 AND guild_id = $2 AND status = 'pending'
            RETURNING *
            """,
            submission_id,
            guild_id,
            reviewer_id,
        )
        return _pack_submission_from_row(row) if row else None

    async def add_pack_value(self, pack_id: int, *, guild_id: int, field: str, value: str, updated_by: int) -> BunkerContentPack | None:
        if field not in PACK_FIELDS:
            raise ValueError(f"Unknown pack field: {field}")
        pack = await self.get_content_pack(pack_id, guild_id=guild_id)
        if pack is None:
            return None
        text = value.strip()[:300]
        if not text:
            return pack
        content = {key: tuple(values) for key, values in pack.content.items()}
        values = list(content.get(field, ()))
        if text not in values:
            values.append(text)
        content[field] = tuple(values)
        return await self.update_content_pack(pack_id, guild_id=guild_id, updated_by=updated_by, content=content)

    async def remove_pack_value(self, pack_id: int, *, guild_id: int, field: str, value: str, updated_by: int) -> BunkerContentPack | None:
        if field not in PACK_FIELDS:
            raise ValueError(f"Unknown pack field: {field}")
        pack = await self.get_content_pack(pack_id, guild_id=guild_id)
        if pack is None:
            return None
        content = {key: tuple(values) for key, values in pack.content.items()}
        content[field] = tuple(candidate for candidate in content.get(field, ()) if candidate != value)
        return await self.update_content_pack(pack_id, guild_id=guild_id, updated_by=updated_by, content=content)

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
        room_index: int,
        text_channel_id: int,
        voice_channel_id: int,
        host_display_name: str,
        is_admin_game: bool = False,
    ) -> BunkerGame:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                active = await connection.fetchrow(
                    """
                    SELECT id
                    FROM bunker_games
                    WHERE guild_id = $1
                      AND host_id = $2
                      AND room_status IN ('lobby', 'active')
                      AND finished_at IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    setup.guild_id,
                    host_id,
                )
                if active is not None:
                    raise ActiveBunkerGameError(int(active["id"]))

                row = await connection.fetchrow(
                    """
                    INSERT INTO bunker_games (
                        guild_id, setup_id, setup_channel_id, setup_message_id, category_id,
                        game_text_channel_id, voice_channel_id, host_id, state, mode, is_public,
                        slots, max_rounds, timer_seconds, settings, is_admin_game, room_kind, room_index,
                        room_status
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'lobby', $9, $10, $11, $12, $13, $14::jsonb, $15, $16, $17, 'lobby')
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
                    settings.room_kind.value,
                    room_index,
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

        return _game_from_row(row)

    async def next_room_index(self, setup: RoomSetup) -> int:
        if setup.category_id is None:
            rows = await self.pool.fetch(
                """
                SELECT room_index
                FROM bunker_games
                WHERE guild_id = $1
                  AND setup_id = $2
                  AND room_status IN ('lobby', 'active')
                  AND finished_at IS NULL
                ORDER BY room_index ASC
                """,
                setup.guild_id,
                setup.id,
            )
        else:
            rows = await self.pool.fetch(
                """
                SELECT room_index
                FROM bunker_games
                WHERE guild_id = $1
                  AND category_id = $2
                  AND room_status IN ('lobby', 'active')
                  AND finished_at IS NULL
                ORDER BY room_index ASC
                """,
                setup.guild_id,
                setup.category_id,
            )
        used = {int(row["room_index"]) for row in rows if int(row["room_index"]) > 0}
        room_index = 1
        while room_index in used:
            room_index += 1
        return room_index

    async def get_active_game_by_setup(self, setup_id: int) -> BunkerGame | None:
        row = await self.pool.fetchrow(
            """
            SELECT *
            FROM bunker_games
            WHERE setup_id = $1
              AND room_status IN ('lobby', 'active')
              AND finished_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            setup_id,
        )
        return _game_from_row(row) if row else None

    async def get_active_game_by_host(self, guild_id: int, host_id: int) -> BunkerGame | None:
        row = await self.pool.fetchrow(
            """
            SELECT *
            FROM bunker_games
            WHERE guild_id = $1
              AND host_id = $2
              AND room_status IN ('lobby', 'active')
              AND finished_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            guild_id,
            host_id,
        )
        return _game_from_row(row) if row else None

    async def list_active_games_by_host(self, guild_id: int, host_id: int) -> list[BunkerGame]:
        rows = await self.pool.fetch(
            """
            SELECT *
            FROM bunker_games
            WHERE guild_id = $1
              AND host_id = $2
              AND room_status IN ('lobby', 'active')
              AND finished_at IS NULL
            ORDER BY id DESC
            """,
            guild_id,
            host_id,
        )
        return [_game_from_row(row) for row in rows]

    async def get_active_game_by_text_channel(self, channel_id: int) -> BunkerGame | None:
        row = await self.pool.fetchrow(
            """
            SELECT *
            FROM bunker_games
            WHERE game_text_channel_id = $1
              AND room_status IN ('lobby', 'active')
              AND finished_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            channel_id,
        )
        return _game_from_row(row) if row else None

    async def get_game_by_text_channel(self, channel_id: int) -> BunkerGame | None:
        row = await self.pool.fetchrow(
            """
            SELECT *
            FROM bunker_games
            WHERE game_text_channel_id = $1
            ORDER BY id DESC
            LIMIT 1
            """,
            channel_id,
        )
        return _game_from_row(row) if row else None

    async def get_game(self, game_id: int) -> BunkerGame | None:
        row = await self.pool.fetchrow("SELECT * FROM bunker_games WHERE id = $1", game_id)
        return _game_from_row(row) if row else None

    async def get_game_by_public_message(self, message_id: int) -> tuple[BunkerGame, str] | None:
        row = await self.pool.fetchrow(
            """
            SELECT g.*, public_messages.key AS public_message_key
            FROM bunker_games AS g
            JOIN LATERAL jsonb_each_text(g.public_message_ids) AS public_messages(key, value) ON TRUE
            WHERE public_messages.value::bigint = $1
              AND g.room_status IN ('lobby', 'active')
              AND g.finished_at IS NULL
            ORDER BY g.id DESC
            LIMIT 1
            """,
            message_id,
        )
        if row is None:
            return None
        return _game_from_row(row), str(row["public_message_key"])

    async def update_game_settings(self, game_id: int, settings: BunkerSettings, *, is_admin_game: bool) -> BunkerGame | None:
        row = await self.pool.fetchrow(
            """
            UPDATE bunker_games
            SET mode = $2,
                is_public = $3,
                slots = $4,
                max_rounds = $5,
                timer_seconds = $6,
                settings = $7::jsonb,
                is_admin_game = $8,
                room_kind = $9,
                updated_at = NOW()
            WHERE id = $1 AND state = 'lobby' AND room_status = 'lobby' AND finished_at IS NULL
            RETURNING *
            """,
            game_id,
            settings.mode.value,
            settings.is_public,
            settings.slots,
            settings.rounds,
            settings.timer_seconds,
            json.dumps(settings.to_json(), ensure_ascii=False),
            is_admin_game,
            settings.room_kind.value,
        )
        return _game_from_row(row) if row else None

    async def set_board_message(self, game_id: int, message_id: int | None) -> None:
        await self.pool.execute(
            "UPDATE bunker_games SET board_message_id = $2, updated_at = NOW() WHERE id = $1",
            game_id,
            message_id,
        )

    async def set_public_message_id(self, game_id: int, key: str, message_id: int) -> None:
        await self.pool.execute(
            """
            UPDATE bunker_games
            SET public_message_ids = jsonb_set(public_message_ids, $2::text[], to_jsonb($3::bigint), TRUE),
                updated_at = NOW()
            WHERE id = $1
            """,
            game_id,
            [key],
            message_id,
        )

    async def remove_public_message_id(self, game_id: int, key: str) -> None:
        await self.pool.execute(
            """
            UPDATE bunker_games
            SET public_message_ids = public_message_ids - $2,
                updated_at = NOW()
            WHERE id = $1
            """,
            game_id,
            key,
        )

    async def set_collapsed_section(self, game_id: int, key: str, collapsed: bool) -> None:
        await self.pool.execute(
            """
            UPDATE bunker_games
            SET collapsed_sections = jsonb_set(collapsed_sections, $2::text[], to_jsonb($3::boolean), TRUE),
                updated_at = NOW()
            WHERE id = $1
            """,
            game_id,
            [key],
            collapsed,
        )

    async def set_turn_order(self, game_id: int, user_ids: list[int]) -> None:
        await self.pool.execute(
            """
            UPDATE bunker_games
            SET turn_order = $2::jsonb,
                current_turn_index = 0,
                reveals_done_this_turn = 0,
                speech_index = 0,
                updated_at = NOW()
            WHERE id = $1
            """,
            game_id,
            json.dumps(user_ids),
        )

    async def set_reveal_progress(self, game_id: int, *, current_turn_index: int, reveals_done_this_turn: int) -> None:
        await self.pool.execute(
            """
            UPDATE bunker_games
            SET current_turn_index = $2,
                reveals_done_this_turn = $3,
                updated_at = NOW()
            WHERE id = $1
            """,
            game_id,
            current_turn_index,
            reveals_done_this_turn,
        )

    async def set_speech_index(self, game_id: int, speech_index: int) -> None:
        await self.pool.execute(
            """
            UPDATE bunker_games
            SET speech_index = $2,
                updated_at = NOW()
            WHERE id = $1
            """,
            game_id,
            speech_index,
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
                room_status = CASE
                    WHEN $2 = 'lobby' THEN 'lobby'
                    WHEN $2 = 'finished' THEN 'finished'
                    ELSE 'active'
                END,
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

    async def record_xp_award_once(self, game_id: int, user_id: int, amount: int) -> bool:
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_xp_awards (game_id, user_id, amount)
            VALUES ($1, $2, $3)
            ON CONFLICT (game_id, user_id) DO NOTHING
            RETURNING game_id
            """,
            game_id,
            user_id,
            amount,
        )
        return row is not None

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

    async def reveal_all_stats(self, game_id: int, user_id: int) -> None:
        await self.pool.execute(
            """
            UPDATE bunker_players
            SET revealed_stats = $3::jsonb,
                final_revealed = TRUE
            WHERE game_id = $1 AND user_id = $2
            """,
            game_id,
            user_id,
            json.dumps(REVEALABLE_STATS, ensure_ascii=False),
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

    async def save_vote(self, vote: Vote) -> bool:
        row = await self.pool.fetchrow(
            """
            INSERT INTO bunker_votes (game_id, round_number, voter_id, target_user_id, is_abstain)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (game_id, round_number, voter_id)
            DO NOTHING
            RETURNING game_id
            """,
            vote.game_id,
            vote.round_number,
            vote.voter_id,
            vote.target_user_id,
            vote.is_abstain,
        )
        return row is not None

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
            WHERE room_status = 'active'
              AND finished_at IS NULL
              AND paused_at IS NULL
              AND phase_ends_at IS NOT NULL
              AND phase_ends_at <= $1
              AND state <> 'reveal_phase'
            ORDER BY phase_ends_at ASC
            """,
            now,
        )
        return [_game_from_row(row) for row in rows]

    async def list_open_games(self, *, limit: int = 100) -> list[BunkerGame]:
        rows = await self.pool.fetch(
            """
            SELECT *
            FROM bunker_games
            WHERE room_status IN ('lobby', 'active')
              AND finished_at IS NULL
            ORDER BY updated_at ASC
            LIMIT $1
            """,
            limit,
        )
        return [_game_from_row(row) for row in rows]

    async def finish_game(self, game_id: int, *, room_status: RoomStatus = RoomStatus.FINISHED) -> None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow("SELECT setup_id FROM bunker_games WHERE id = $1", game_id)
                await connection.execute(
                    """
                    UPDATE bunker_games
                    SET state = 'finished',
                        room_status = $2,
                        finished_at = COALESCE(finished_at, NOW()),
                        phase_ends_at = NULL,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    game_id,
                    room_status.value,
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

    async def close_game(self, game_id: int) -> None:
        await self.finish_game(game_id, room_status=RoomStatus.CLOSED)

    async def crash_game(self, game_id: int) -> None:
        await self.finish_game(game_id, room_status=RoomStatus.CRASHED)


class ActiveBunkerGameError(RuntimeError):
    def __init__(self, game_id: int) -> None:
        super().__init__(f"Active bunker game already exists: {game_id}")
        self.game_id = game_id


def _pack_content_to_json(content: dict[str, tuple[str, ...]]) -> dict[str, list[str]]:
    normalized = normalize_pack_content(content)
    return {field: list(normalized[field]) for field in PACK_FIELDS}


def _content_pack_from_row(row: asyncpg.Record) -> BunkerContentPack:
    return BunkerContentPack(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        name=str(row["name"]),
        description=str(row["description"] or ""),
        content=normalize_pack_content(_json_load(row["content"], {})),
        is_enabled=bool(row["is_enabled"]),
        created_by=int(row["created_by"]),
        updated_by=int(row["updated_by"]) if row["updated_by"] is not None else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _builder_progress_from_row(row: asyncpg.Record) -> BunkerBuilderProgress:
    return BunkerBuilderProgress(
        guild_id=int(row["guild_id"]),
        user_id=int(row["user_id"]),
        agreement_version=int(row["agreement_version"]),
        accepted_agreement_at=row["accepted_agreement_at"],
        completed_at=row["completed_at"],
    )


def _pack_submission_from_row(row: asyncpg.Record) -> BunkerPackSubmission:
    raw_status = str(row["status"] or PackSubmissionStatus.PENDING.value)
    try:
        status = PackSubmissionStatus(raw_status)
    except ValueError:
        status = PackSubmissionStatus.PENDING
    return BunkerPackSubmission(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        author_id=int(row["author_id"]),
        status=status,
        name=str(row["name"] or "Новый пак"),
        description=str(row["description"] or ""),
        content=normalize_pack_content(_json_load(row["content"], {})),
        source_filename=str(row["source_filename"] or ""),
        reviewer_id=int(row["reviewer_id"]) if row["reviewer_id"] is not None else None,
        content_pack_id=int(row["content_pack_id"]) if row["content_pack_id"] is not None else None,
        created_at=row["created_at"],
        reviewed_at=row["reviewed_at"],
    )


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
        interest_role_id=int(row["interest_role_id"]) if row["interest_role_id"] is not None else None,
        builder_reward_role_id=int(row["builder_reward_role_id"]) if row["builder_reward_role_id"] is not None else None,
        builder_info_channel_id=int(row["builder_info_channel_id"]) if row["builder_info_channel_id"] is not None else None,
    )


def _game_from_row(row: asyncpg.Record) -> BunkerGame:
    settings = BunkerSettings.from_json(_json_load(row["settings"]))
    raw_room_kind = str(row["room_kind"] or settings.room_kind.value)
    row_room_kind = RoomKind.ADMIN_TEST if raw_room_kind == RoomKind.ADMIN_TEST.value else RoomKind.RANKED
    if settings.room_kind != row_room_kind or settings.is_ranked != (row_room_kind == RoomKind.RANKED):
        settings = replace(
            settings,
            room_kind=row_room_kind,
            is_ranked=row_room_kind == RoomKind.RANKED,
            is_public=False if row_room_kind == RoomKind.ADMIN_TEST else settings.is_public,
        )
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
        room_index=int(row["room_index"]),
        room_status=RoomStatus(str(row["room_status"] or RoomStatus.LOBBY.value)),
        is_admin_game=bool(row["is_admin_game"]),
        room_kind=row_room_kind,
        public_message_ids={str(key): int(value) for key, value in _json_load(row["public_message_ids"], {}).items()},
        turn_order=tuple(int(user_id) for user_id in _json_load(row["turn_order"], [])),
        current_turn_index=int(row["current_turn_index"]),
        reveals_done_this_turn=int(row["reveals_done_this_turn"]),
        speech_index=int(row["speech_index"]),
        collapsed_sections={str(key): bool(value) for key, value in _json_load(row["collapsed_sections"], {}).items()},
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
        final_revealed=bool(row["final_revealed"]),
    )


def _vote_from_row(row: asyncpg.Record) -> Vote:
    return Vote(
        game_id=int(row["game_id"]),
        round_number=int(row["round_number"]),
        voter_id=int(row["voter_id"]),
        target_user_id=int(row["target_user_id"]) if row["target_user_id"] is not None else None,
        is_abstain=bool(row["is_abstain"]),
        confirmed_at=row["confirmed_at"],
    )


def _json_load(raw: Any, default: Any = None) -> Any:
    if raw is None:
        return default
    if isinstance(raw, str):
        return json.loads(raw)
    return raw
