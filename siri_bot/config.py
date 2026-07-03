from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    token: str
    database_url: str
    guild_id: int | None = None
    owner_id: int | None = None
    admin_role_ids: frozenset[int] = field(default_factory=frozenset)
    command_prefix: str = "!"
    bot_status: str = "online"
    log_level: str = "INFO"


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token or token == "put-your-bot-token-here":
        raise RuntimeError("DISCORD_TOKEN is required. Copy .env.example to .env and set the token.")

    return Settings(
        token=token,
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql://siri:siri_password_change_me@localhost:5432/siri_discord_bot",
        ).strip(),
        guild_id=_optional_int("GUILD_ID"),
        owner_id=_optional_int("OWNER_ID"),
        admin_role_ids=_int_set(os.getenv("ADMIN_ROLE_IDS", "")),
        command_prefix=os.getenv("COMMAND_PREFIX", "!").strip() or "!",
        bot_status=os.getenv("BOT_STATUS", "online").strip() or "online",
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None

    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a Discord numeric ID.") from exc


def _int_set(raw: str) -> frozenset[int]:
    if not raw.strip():
        return frozenset()

    values: set[int] = set()
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue

        try:
            values.add(int(value))
        except ValueError as exc:
            raise RuntimeError("ADMIN_ROLE_IDS must contain comma-separated Discord role IDs.") from exc

    return frozenset(values)
