from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

import asyncpg

from siri_bot.leveling.formula import FormulaConfig, level_for_total_xp, resolve_booster_multiplier
from siri_bot.leveling.models import Booster, LeaderboardEntry, LevelingSettings, VoiceSession, XpChange


DEFAULT_LEVELUP_MESSAGE = "{user} достиг(ла) уровня {level}."


class LevelingRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def init_schema(self) -> None:
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS leveling_guild_settings (
                    guild_id BIGINT PRIMARY KEY,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    formula_preset TEXT NOT NULL DEFAULT 'quadratic',
                    formula_a DOUBLE PRECISION NOT NULL DEFAULT 5,
                    formula_b DOUBLE PRECISION NOT NULL DEFAULT 50,
                    formula_c DOUBLE PRECISION NOT NULL DEFAULT 100,
                    message_xp_min INTEGER NOT NULL DEFAULT 15,
                    message_xp_max INTEGER NOT NULL DEFAULT 25,
                    message_cooldown_seconds INTEGER NOT NULL DEFAULT 60,
                    voice_xp_per_minute INTEGER NOT NULL DEFAULT 2,
                    reaction_xp INTEGER NOT NULL DEFAULT 2,
                    reaction_cooldown_seconds INTEGER NOT NULL DEFAULT 60,
                    role_reward_mode TEXT NOT NULL DEFAULT 'accumulative',
                    levelup_channel_id BIGINT,
                    levelup_message TEXT NOT NULL DEFAULT '{user} достиг(ла) уровня {level}.',
                    first_place_role_id BIGINT,
                    first_place_user_id BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS leveling_member_xp (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    total_xp BIGINT NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS leveling_xp_cooldowns (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    source TEXT NOT NULL,
                    available_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (guild_id, user_id, source)
                );

                CREATE TABLE IF NOT EXISTS leveling_reaction_awards (
                    guild_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    reactor_id BIGINT NOT NULL,
                    emoji_key TEXT NOT NULL,
                    author_id BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (guild_id, message_id, reactor_id, emoji_key)
                );

                CREATE TABLE IF NOT EXISTS leveling_role_rewards (
                    guild_id BIGINT NOT NULL,
                    level INTEGER NOT NULL,
                    role_id BIGINT NOT NULL,
                    PRIMARY KEY (guild_id, level)
                );

                CREATE TABLE IF NOT EXISTS leveling_xp_boosters (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    scope TEXT NOT NULL,
                    target_id BIGINT,
                    multiplier DOUBLE PRECISION NOT NULL,
                    expires_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS leveling_voice_sessions (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS idx_leveling_member_xp_leaderboard
                    ON leveling_member_xp (guild_id, total_xp DESC, updated_at ASC);
                CREATE INDEX IF NOT EXISTS idx_leveling_boosters_active
                    ON leveling_xp_boosters (guild_id, scope, target_id, expires_at);
                """
            )

    async def get_settings(self, guild_id: int) -> LevelingSettings:
        row = await self._ensure_settings(guild_id)
        return _settings_from_row(row)

    async def update_enabled(self, guild_id: int, enabled: bool) -> LevelingSettings:
        await self._ensure_settings(guild_id)
        row = await self.pool.fetchrow(
            """
            UPDATE leveling_guild_settings
            SET enabled = $2, updated_at = NOW()
            WHERE guild_id = $1
            RETURNING *
            """,
            guild_id,
            enabled,
        )
        return _settings_from_row(row or await self._ensure_settings(guild_id))

    async def update_formula(self, guild_id: int, config: FormulaConfig) -> LevelingSettings:
        await self._ensure_settings(guild_id)
        row = await self.pool.fetchrow(
            """
            UPDATE leveling_guild_settings
            SET formula_preset = $2,
                formula_a = $3,
                formula_b = $4,
                formula_c = $5,
                updated_at = NOW()
            WHERE guild_id = $1
            RETURNING *
            """,
            guild_id,
            config.preset,
            config.a,
            config.b,
            config.c,
        )
        return _settings_from_row(row)

    async def update_xp_options(
        self,
        guild_id: int,
        *,
        message_xp_min: int,
        message_xp_max: int,
        message_cooldown_seconds: int,
        voice_xp_per_minute: int,
        reaction_xp: int,
        reaction_cooldown_seconds: int,
    ) -> LevelingSettings:
        await self._ensure_settings(guild_id)
        row = await self.pool.fetchrow(
            """
            UPDATE leveling_guild_settings
            SET message_xp_min = $2,
                message_xp_max = $3,
                message_cooldown_seconds = $4,
                voice_xp_per_minute = $5,
                reaction_xp = $6,
                reaction_cooldown_seconds = $7,
                updated_at = NOW()
            WHERE guild_id = $1
            RETURNING *
            """,
            guild_id,
            message_xp_min,
            message_xp_max,
            message_cooldown_seconds,
            voice_xp_per_minute,
            reaction_xp,
            reaction_cooldown_seconds,
        )
        return _settings_from_row(row)

    async def update_levelup_channel(self, guild_id: int, channel_id: int | None) -> LevelingSettings:
        await self._ensure_settings(guild_id)
        row = await self.pool.fetchrow(
            """
            UPDATE leveling_guild_settings
            SET levelup_channel_id = $2, updated_at = NOW()
            WHERE guild_id = $1
            RETURNING *
            """,
            guild_id,
            channel_id,
        )
        return _settings_from_row(row)

    async def update_levelup_message(self, guild_id: int, message: str) -> LevelingSettings:
        await self._ensure_settings(guild_id)
        row = await self.pool.fetchrow(
            """
            UPDATE leveling_guild_settings
            SET levelup_message = $2, updated_at = NOW()
            WHERE guild_id = $1
            RETURNING *
            """,
            guild_id,
            message,
        )
        return _settings_from_row(row)

    async def update_reward_mode(self, guild_id: int, mode: str) -> LevelingSettings:
        await self._ensure_settings(guild_id)
        row = await self.pool.fetchrow(
            """
            UPDATE leveling_guild_settings
            SET role_reward_mode = $2, updated_at = NOW()
            WHERE guild_id = $1
            RETURNING *
            """,
            guild_id,
            mode,
        )
        return _settings_from_row(row)

    async def update_first_place_role(self, guild_id: int, role_id: int | None) -> LevelingSettings:
        await self._ensure_settings(guild_id)
        row = await self.pool.fetchrow(
            """
            UPDATE leveling_guild_settings
            SET first_place_role_id = $2, updated_at = NOW()
            WHERE guild_id = $1
            RETURNING *
            """,
            guild_id,
            role_id,
        )
        return _settings_from_row(row)

    async def update_first_place_user(self, guild_id: int, user_id: int | None) -> None:
        await self._ensure_settings(guild_id)
        await self.pool.execute(
            """
            UPDATE leveling_guild_settings
            SET first_place_user_id = $2, updated_at = NOW()
            WHERE guild_id = $1
            """,
            guild_id,
            user_id,
        )

    async def try_acquire_cooldown(self, guild_id: int, user_id: int, source: str, cooldown_seconds: int) -> bool:
        if cooldown_seconds <= 0:
            return True

        row = await self.pool.fetchrow(
            """
            INSERT INTO leveling_xp_cooldowns (guild_id, user_id, source, available_at)
            VALUES ($1, $2, $3, NOW() + ($4::INTEGER * INTERVAL '1 second'))
            ON CONFLICT (guild_id, user_id, source)
            DO UPDATE SET available_at = EXCLUDED.available_at
            WHERE leveling_xp_cooldowns.available_at <= NOW()
            RETURNING available_at
            """,
            guild_id,
            user_id,
            source,
            cooldown_seconds,
        )
        return row is not None

    async def add_xp(self, guild_id: int, user_id: int, amount: int, config: FormulaConfig) -> XpChange:
        delta = max(0, amount)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO leveling_member_xp (guild_id, user_id, total_xp)
                    VALUES ($1, $2, 0)
                    ON CONFLICT (guild_id, user_id) DO NOTHING
                    """,
                    guild_id,
                    user_id,
                )
                row = await connection.fetchrow(
                    """
                    SELECT total_xp
                    FROM leveling_member_xp
                    WHERE guild_id = $1 AND user_id = $2
                    FOR UPDATE
                    """,
                    guild_id,
                    user_id,
                )
                old_total = int(row["total_xp"])
                new_total = old_total + delta
                await connection.execute(
                    """
                    UPDATE leveling_member_xp
                    SET total_xp = $3, updated_at = NOW()
                    WHERE guild_id = $1 AND user_id = $2
                    """,
                    guild_id,
                    user_id,
                    new_total,
                )

        return XpChange(
            guild_id=guild_id,
            user_id=user_id,
            old_total_xp=old_total,
            new_total_xp=new_total,
            old_level=level_for_total_xp(old_total, config),
            new_level=level_for_total_xp(new_total, config),
            amount=delta,
        )

    async def set_xp(self, guild_id: int, user_id: int, total_xp: int, config: FormulaConfig) -> XpChange:
        new_total = max(0, total_xp)
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO leveling_member_xp (guild_id, user_id, total_xp)
                    VALUES ($1, $2, 0)
                    ON CONFLICT (guild_id, user_id) DO NOTHING
                    """,
                    guild_id,
                    user_id,
                )
                row = await connection.fetchrow(
                    """
                    SELECT total_xp
                    FROM leveling_member_xp
                    WHERE guild_id = $1 AND user_id = $2
                    FOR UPDATE
                    """,
                    guild_id,
                    user_id,
                )
                old_total = int(row["total_xp"])
                await connection.execute(
                    """
                    UPDATE leveling_member_xp
                    SET total_xp = $3, updated_at = NOW()
                    WHERE guild_id = $1 AND user_id = $2
                    """,
                    guild_id,
                    user_id,
                    new_total,
                )

        return XpChange(
            guild_id=guild_id,
            user_id=user_id,
            old_total_xp=old_total,
            new_total_xp=new_total,
            old_level=level_for_total_xp(old_total, config),
            new_level=level_for_total_xp(new_total, config),
            amount=new_total - old_total,
        )

    async def get_member_xp(self, guild_id: int, user_id: int) -> int:
        row = await self.pool.fetchrow(
            """
            SELECT total_xp
            FROM leveling_member_xp
            WHERE guild_id = $1 AND user_id = $2
            """,
            guild_id,
            user_id,
        )
        return int(row["total_xp"]) if row else 0

    async def get_member_rank(self, guild_id: int, user_id: int) -> int:
        total_xp = await self.get_member_xp(guild_id, user_id)
        row = await self.pool.fetchrow(
            """
            SELECT COUNT(*) AS better_count
            FROM leveling_member_xp
            WHERE guild_id = $1 AND total_xp > $2
            """,
            guild_id,
            total_xp,
        )
        return int(row["better_count"]) + 1

    async def get_leaderboard(self, guild_id: int, *, limit: int, offset: int) -> list[LeaderboardEntry]:
        rows = await self.pool.fetch(
            """
            SELECT user_id, total_xp
            FROM leveling_member_xp
            WHERE guild_id = $1
            ORDER BY total_xp DESC, updated_at ASC, user_id ASC
            LIMIT $2 OFFSET $3
            """,
            guild_id,
            limit,
            offset,
        )
        return [
            LeaderboardEntry(user_id=int(row["user_id"]), total_xp=int(row["total_xp"]), rank=offset + index + 1)
            for index, row in enumerate(rows)
        ]

    async def get_leader(self, guild_id: int) -> LeaderboardEntry | None:
        rows = await self.get_leaderboard(guild_id, limit=1, offset=0)
        return rows[0] if rows else None

    async def upsert_role_reward(self, guild_id: int, level: int, role_id: int) -> None:
        await self.pool.execute(
            """
            INSERT INTO leveling_role_rewards (guild_id, level, role_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, level)
            DO UPDATE SET role_id = EXCLUDED.role_id
            """,
            guild_id,
            level,
            role_id,
        )

    async def remove_role_reward(self, guild_id: int, level: int) -> bool:
        result = await self.pool.execute(
            """
            DELETE FROM leveling_role_rewards
            WHERE guild_id = $1 AND level = $2
            """,
            guild_id,
            level,
        )
        return result.endswith(" 1")

    async def get_role_rewards(self, guild_id: int) -> list[tuple[int, int]]:
        rows = await self.pool.fetch(
            """
            SELECT level, role_id
            FROM leveling_role_rewards
            WHERE guild_id = $1
            ORDER BY level ASC
            """,
            guild_id,
        )
        return [(int(row["level"]), int(row["role_id"])) for row in rows]

    async def add_booster(
        self,
        guild_id: int,
        scope: str,
        target_id: int | None,
        multiplier: float,
        expires_at: datetime | None,
    ) -> Booster:
        row = await self.pool.fetchrow(
            """
            INSERT INTO leveling_xp_boosters (guild_id, scope, target_id, multiplier, expires_at)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, scope, target_id, multiplier, expires_at
            """,
            guild_id,
            scope,
            target_id,
            multiplier,
            expires_at,
        )
        return _booster_from_row(row)

    async def remove_booster(self, guild_id: int, booster_id: int) -> bool:
        result = await self.pool.execute(
            """
            DELETE FROM leveling_xp_boosters
            WHERE guild_id = $1 AND id = $2
            """,
            guild_id,
            booster_id,
        )
        return result.endswith(" 1")

    async def get_boosters(self, guild_id: int) -> list[Booster]:
        rows = await self.pool.fetch(
            """
            SELECT id, scope, target_id, multiplier, expires_at
            FROM leveling_xp_boosters
            WHERE guild_id = $1 AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY id ASC
            """,
            guild_id,
        )
        return [_booster_from_row(row) for row in rows]

    async def get_booster_multiplier(self, guild_id: int, user_id: int, role_ids: Sequence[int]) -> float:
        rows = await self.pool.fetch(
            """
            SELECT scope, multiplier
            FROM leveling_xp_boosters
            WHERE guild_id = $1
              AND (expires_at IS NULL OR expires_at > NOW())
              AND (
                scope = 'global'
                OR (scope = 'user' AND target_id = $2)
                OR (scope = 'role' AND target_id = ANY($3::BIGINT[]))
              )
            """,
            guild_id,
            user_id,
            list(role_ids),
        )
        return resolve_booster_multiplier(rows)

    async def register_reaction_award(
        self,
        guild_id: int,
        message_id: int,
        reactor_id: int,
        emoji_key: str,
        author_id: int,
    ) -> bool:
        row = await self.pool.fetchrow(
            """
            INSERT INTO leveling_reaction_awards (guild_id, message_id, reactor_id, emoji_key, author_id)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (guild_id, message_id, reactor_id, emoji_key) DO NOTHING
            RETURNING message_id
            """,
            guild_id,
            message_id,
            reactor_id,
            emoji_key,
            author_id,
        )
        return row is not None

    async def upsert_voice_session(self, guild_id: int, user_id: int, channel_id: int) -> None:
        await self.pool.execute(
            """
            INSERT INTO leveling_voice_sessions (guild_id, user_id, channel_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, user_id)
            DO UPDATE SET channel_id = EXCLUDED.channel_id
            """,
            guild_id,
            user_id,
            channel_id,
        )

    async def remove_voice_session(self, guild_id: int, user_id: int) -> None:
        await self.pool.execute(
            """
            DELETE FROM leveling_voice_sessions
            WHERE guild_id = $1 AND user_id = $2
            """,
            guild_id,
            user_id,
        )

    async def get_voice_sessions(self) -> list[VoiceSession]:
        rows = await self.pool.fetch(
            """
            SELECT guild_id, user_id, channel_id
            FROM leveling_voice_sessions
            ORDER BY guild_id, user_id
            """
        )
        return [
            VoiceSession(guild_id=int(row["guild_id"]), user_id=int(row["user_id"]), channel_id=int(row["channel_id"]))
            for row in rows
        ]

    async def touch_voice_session(self, guild_id: int, user_id: int) -> None:
        await self.pool.execute(
            """
            UPDATE leveling_voice_sessions
            SET last_awarded_at = NOW()
            WHERE guild_id = $1 AND user_id = $2
            """,
            guild_id,
            user_id,
        )

    async def reset_member(self, guild_id: int, user_id: int) -> None:
        await self.pool.execute(
            """
            DELETE FROM leveling_member_xp
            WHERE guild_id = $1 AND user_id = $2
            """,
            guild_id,
            user_id,
        )

    async def reset_guild_progress(self, guild_id: int) -> None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("DELETE FROM leveling_member_xp WHERE guild_id = $1", guild_id)
                await connection.execute("DELETE FROM leveling_xp_cooldowns WHERE guild_id = $1", guild_id)
                await connection.execute("DELETE FROM leveling_reaction_awards WHERE guild_id = $1", guild_id)
                await connection.execute("DELETE FROM leveling_voice_sessions WHERE guild_id = $1", guild_id)
                await connection.execute(
                    """
                    UPDATE leveling_guild_settings
                    SET first_place_user_id = NULL, updated_at = NOW()
                    WHERE guild_id = $1
                    """,
                    guild_id,
                )

    async def _ensure_settings(self, guild_id: int) -> asyncpg.Record:
        await self.pool.execute(
            """
            INSERT INTO leveling_guild_settings (guild_id)
            VALUES ($1)
            ON CONFLICT (guild_id) DO NOTHING
            """,
            guild_id,
        )
        return await self.pool.fetchrow(
            """
            SELECT *
            FROM leveling_guild_settings
            WHERE guild_id = $1
            """,
            guild_id,
        )


def _settings_from_row(row: asyncpg.Record) -> LevelingSettings:
    return LevelingSettings(
        guild_id=int(row["guild_id"]),
        enabled=bool(row["enabled"]),
        formula_preset=str(row["formula_preset"]),
        formula_a=float(row["formula_a"]),
        formula_b=float(row["formula_b"]),
        formula_c=float(row["formula_c"]),
        message_xp_min=int(row["message_xp_min"]),
        message_xp_max=int(row["message_xp_max"]),
        message_cooldown_seconds=int(row["message_cooldown_seconds"]),
        voice_xp_per_minute=int(row["voice_xp_per_minute"]),
        reaction_xp=int(row["reaction_xp"]),
        reaction_cooldown_seconds=int(row["reaction_cooldown_seconds"]),
        role_reward_mode=str(row["role_reward_mode"]),
        levelup_channel_id=int(row["levelup_channel_id"]) if row["levelup_channel_id"] is not None else None,
        levelup_message=str(row["levelup_message"] or DEFAULT_LEVELUP_MESSAGE),
        first_place_role_id=int(row["first_place_role_id"]) if row["first_place_role_id"] is not None else None,
        first_place_user_id=int(row["first_place_user_id"]) if row["first_place_user_id"] is not None else None,
    )


def _booster_from_row(row: asyncpg.Record) -> Booster:
    return Booster(
        id=int(row["id"]),
        scope=str(row["scope"]),
        target_id=int(row["target_id"]) if row["target_id"] is not None else None,
        multiplier=float(row["multiplier"]),
        expires_at=row["expires_at"],
    )
