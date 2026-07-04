from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class GameMode(StrEnum):
    CLASSIC = "classic"
    MEME = "meme"
    HARDCORE = "hardcore"
    TURBO = "turbo"
    TRAITOR = "traitor"


class GameState(StrEnum):
    LOBBY = "lobby"
    PREPARING = "preparing"
    REVEAL_PHASE = "reveal_phase"
    DISCUSSION_PHASE = "discussion_phase"
    CHAOS_PHASE = "chaos_phase"
    VOTING_PHASE = "voting_phase"
    ELIMINATION_PHASE = "elimination_phase"
    FINAL_PHASE = "final_phase"
    FINISHED = "finished"


class VotePolicy(StrEnum):
    ABSTAIN = "abstain"
    RANDOM = "random"


CARD_STAT_LABELS: dict[str, str] = {
    "profession": "Профессия",
    "age": "Возраст",
    "health": "Здоровье",
    "skill": "Навык",
    "item": "Предмет",
    "phobia": "Фобия",
    "secret": "Секрет",
    "funny_trait": "Черта",
    "special_action": "Спец-действие",
}

REVEALABLE_STATS: tuple[str, ...] = (
    "profession",
    "age",
    "health",
    "skill",
    "item",
    "phobia",
    "secret",
    "funny_trait",
)


@dataclass(frozen=True)
class BunkerSettings:
    mode: GameMode = GameMode.CLASSIC
    slots: int = 8
    rounds: int = 4
    timer_seconds: int = 180
    is_public: bool = True
    explain_for_newbies: bool = True
    missing_vote_policy: VotePolicy = VotePolicy.ABSTAIN

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "slots": self.slots,
            "rounds": self.rounds,
            "timer_seconds": self.timer_seconds,
            "is_public": self.is_public,
            "explain_for_newbies": self.explain_for_newbies,
            "missing_vote_policy": self.missing_vote_policy.value,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> "BunkerSettings":
        if not raw:
            return cls()

        return cls(
            mode=GameMode(str(raw.get("mode", GameMode.CLASSIC.value))),
            slots=int(raw.get("slots", 8)),
            rounds=int(raw.get("rounds", 4)),
            timer_seconds=int(raw.get("timer_seconds", 180)),
            is_public=bool(raw.get("is_public", True)),
            explain_for_newbies=bool(raw.get("explain_for_newbies", True)),
            missing_vote_policy=VotePolicy(str(raw.get("missing_vote_policy", VotePolicy.ABSTAIN.value))),
        )


@dataclass(frozen=True)
class RoomSetup:
    id: int
    guild_id: int
    setup_channel_id: int
    category_id: int | None
    setup_message_id: int | None
    room_name: str
    active_game_id: int | None


@dataclass(frozen=True)
class CharacterCard:
    profession: str
    age: str
    health: str
    skill: str
    item: str
    phobia: str
    secret: str
    funny_trait: str
    special_action: str
    traitor: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "profession": self.profession,
            "age": self.age,
            "health": self.health,
            "skill": self.skill,
            "item": self.item,
            "phobia": self.phobia,
            "secret": self.secret,
            "funny_trait": self.funny_trait,
            "special_action": self.special_action,
            "traitor": self.traitor,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> "CharacterCard | None":
        if not raw:
            return None

        return cls(
            profession=str(raw.get("profession", "")),
            age=str(raw.get("age", "")),
            health=str(raw.get("health", "")),
            skill=str(raw.get("skill", "")),
            item=str(raw.get("item", "")),
            phobia=str(raw.get("phobia", "")),
            secret=str(raw.get("secret", "")),
            funny_trait=str(raw.get("funny_trait", "")),
            special_action=str(raw.get("special_action", "")),
            traitor=bool(raw.get("traitor", False)),
        )


@dataclass(frozen=True)
class BunkerResources:
    food: int = 70
    water: int = 70
    electricity: int = 70
    morale: int = 70
    radiation: int = 20

    def to_json(self) -> dict[str, int]:
        return {
            "food": self.food,
            "water": self.water,
            "electricity": self.electricity,
            "morale": self.morale,
            "radiation": self.radiation,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> "BunkerResources":
        if not raw:
            return cls()

        return cls(
            food=int(raw.get("food", 70)),
            water=int(raw.get("water", 70)),
            electricity=int(raw.get("electricity", 70)),
            morale=int(raw.get("morale", 70)),
            radiation=int(raw.get("radiation", 20)),
        )

    def clamp(self) -> "BunkerResources":
        return BunkerResources(
            food=max(0, min(100, self.food)),
            water=max(0, min(100, self.water)),
            electricity=max(0, min(100, self.electricity)),
            morale=max(0, min(100, self.morale)),
            radiation=max(0, min(100, self.radiation)),
        )


@dataclass(frozen=True)
class BunkerProfile:
    apocalypse: str
    layout: str
    defect: str
    resources: BunkerResources

    def to_json(self) -> dict[str, Any]:
        return {
            "apocalypse": self.apocalypse,
            "layout": self.layout,
            "defect": self.defect,
            "resources": self.resources.to_json(),
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> "BunkerProfile | None":
        if not raw:
            return None

        return cls(
            apocalypse=str(raw.get("apocalypse", "")),
            layout=str(raw.get("layout", "")),
            defect=str(raw.get("defect", "")),
            resources=BunkerResources.from_json(raw.get("resources")),
        )


@dataclass(frozen=True)
class BunkerGame:
    id: int
    guild_id: int
    setup_id: int
    setup_channel_id: int
    setup_message_id: int | None
    category_id: int | None
    game_text_channel_id: int | None
    voice_channel_id: int | None
    host_id: int
    state: GameState
    settings: BunkerSettings
    round_number: int
    phase_started_at: datetime | None
    phase_ends_at: datetime | None
    paused_at: datetime | None
    board_message_id: int | None
    profile: BunkerProfile | None
    recent_events: tuple[str, ...] = field(default_factory=tuple)
    finished_at: datetime | None = None


@dataclass(frozen=True)
class BunkerPlayer:
    game_id: int
    user_id: int
    display_name: str
    is_host: bool
    ready_at: datetime | None
    invited_at: datetime | None
    joined_at: datetime | None
    left_at: datetime | None
    is_eliminated: bool
    card: CharacterCard | None
    revealed_stats: tuple[str, ...]
    used_special_action: bool
    immune_round: int | None
    personal_bonus: int = 0

    @property
    def is_active(self) -> bool:
        return self.left_at is None

    @property
    def is_alive(self) -> bool:
        return self.is_active and not self.is_eliminated


@dataclass(frozen=True)
class Vote:
    game_id: int
    round_number: int
    voter_id: int
    target_user_id: int | None
    is_abstain: bool

