from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from siri_bot.leveling.formula import FormulaConfig


@dataclass(frozen=True)
class LevelingSettings:
    guild_id: int
    enabled: bool
    formula_preset: str
    formula_a: float
    formula_b: float
    formula_c: float
    message_xp_min: int
    message_xp_max: int
    message_cooldown_seconds: int
    voice_xp_per_minute: int
    reaction_xp: int
    reaction_cooldown_seconds: int
    role_reward_mode: str
    levelup_channel_id: int | None
    levelup_message: str
    first_place_role_id: int | None
    first_place_user_id: int | None

    @property
    def formula(self) -> FormulaConfig:
        return FormulaConfig(
            preset=self.formula_preset,
            a=self.formula_a,
            b=self.formula_b,
            c=self.formula_c,
        )


@dataclass(frozen=True)
class XpChange:
    guild_id: int
    user_id: int
    old_total_xp: int
    new_total_xp: int
    old_level: int
    new_level: int
    amount: int


@dataclass(frozen=True)
class LeaderboardEntry:
    user_id: int
    total_xp: int
    rank: int


@dataclass(frozen=True)
class PendingLevelupAnnouncement:
    guild_id: int
    user_id: int
    total_xp: int
    current_level: int
    last_levelup_announced_level: int


@dataclass(frozen=True)
class Booster:
    id: int
    scope: str
    target_id: int | None
    multiplier: float
    expires_at: datetime | None


@dataclass(frozen=True)
class VoiceSession:
    guild_id: int
    user_id: int
    channel_id: int

