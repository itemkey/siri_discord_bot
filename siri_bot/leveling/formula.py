from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping, Sequence


SUPPORTED_FORMULAS = {"linear", "quadratic"}
SUPPORTED_REWARD_MODES = {"accumulative", "highest_only"}
BOOSTER_CAP = 5.0


@dataclass(frozen=True)
class FormulaConfig:
    preset: str = "quadratic"
    a: float = 5.0
    b: float = 50.0
    c: float = 100.0


@dataclass(frozen=True)
class LevelProgress:
    level: int
    current_level_xp: int
    next_level_xp: int
    total_xp: int


def normalize_formula(config: FormulaConfig) -> FormulaConfig:
    preset = config.preset if config.preset in SUPPORTED_FORMULAS else "quadratic"
    return FormulaConfig(preset=preset, a=max(0.0, config.a), b=max(0.0, config.b), c=max(1.0, config.c))


def xp_for_next_level(level: int, config: FormulaConfig) -> int:
    normalized = normalize_formula(config)
    safe_level = max(0, level)

    if normalized.preset == "linear":
        value = normalized.b * safe_level + normalized.c
    else:
        value = normalized.a * safe_level * safe_level + normalized.b * safe_level + normalized.c

    return max(1, round(value))


def level_for_total_xp(total_xp: int, config: FormulaConfig) -> int:
    remaining = max(0, total_xp)
    level = 0

    while level < 100_000:
        needed = xp_for_next_level(level, config)
        if remaining < needed:
            return level

        remaining -= needed
        level += 1

    return level


def progress_for_total_xp(total_xp: int, config: FormulaConfig) -> LevelProgress:
    remaining = max(0, total_xp)
    level = 0

    while level < 100_000:
        needed = xp_for_next_level(level, config)
        if remaining < needed:
            return LevelProgress(level=level, current_level_xp=remaining, next_level_xp=needed, total_xp=max(0, total_xp))

        remaining -= needed
        level += 1

    return LevelProgress(level=level, current_level_xp=0, next_level_xp=1, total_xp=max(0, total_xp))


def reward_roles_for_level(rewards: Sequence[tuple[int, int]], level: int, mode: str) -> set[int]:
    eligible = [(reward_level, role_id) for reward_level, role_id in rewards if reward_level <= level]
    if not eligible:
        return set()

    if mode == "highest_only":
        return {max(eligible, key=lambda item: item[0])[1]}

    return {role_id for _, role_id in eligible}


def resolve_booster_multiplier(rows: Iterable[Mapping[str, object]], cap: float = BOOSTER_CAP) -> float:
    best_by_scope = {"global": 1.0, "user": 1.0, "role": 1.0}

    for row in rows:
        scope = str(row["scope"])
        if scope not in best_by_scope:
            continue

        multiplier = float(row["multiplier"])
        if multiplier > best_by_scope[scope]:
            best_by_scope[scope] = multiplier

    return min(cap, best_by_scope["global"] * best_by_scope["user"] * best_by_scope["role"])


def is_cooldown_available(now: datetime, available_at: datetime | None) -> bool:
    return available_at is None or available_at <= now


def first_place_changed(previous_user_id: int | None, current_user_id: int | None) -> bool:
    return previous_user_id != current_user_id

