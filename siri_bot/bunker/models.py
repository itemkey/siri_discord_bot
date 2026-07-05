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
    SPEECH_PHASE = "speech_phase"
    DISCUSSION_PHASE = "discussion_phase"
    CHAOS_PHASE = "chaos_phase"
    VOTING_PHASE = "voting_phase"
    ELIMINATION_PHASE = "elimination_phase"
    FINAL_PHASE = "final_phase"
    FINISHED = "finished"


class RoomStatus(StrEnum):
    LOBBY = "lobby"
    ACTIVE = "active"
    FINISHED = "finished"
    CLOSED = "closed"
    CRASHED = "crashed"


class VotePolicy(StrEnum):
    ABSTAIN = "abstain"
    RANDOM = "random"


class RoomKind(StrEnum):
    RANKED = "ranked"
    CASUAL = "casual"
    ADMIN_TEST = "admin_test"


CARD_STAT_LABELS: dict[str, str] = {
    "gender": "Пол",
    "body": "Телосложение",
    "age": "Возраст",
    "profession": "Профессия",
    "health": "Здоровье",
    "skill": "Навык",
    "phobia": "Фобия",
    "inventory": "Инвентарь",
    "fact": "Факт",
}

REVEALABLE_STATS: tuple[str, ...] = (
    "gender",
    "body",
    "age",
    "profession",
    "health",
    "skill",
    "phobia",
    "inventory",
    "fact",
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
    content_pack_id: int | None = None
    is_ranked: bool = True
    room_kind: RoomKind = RoomKind.RANKED
    min_players: int = 6
    bunker_seats: int | None = None
    speech_seconds: int = 60
    discussion_seconds: int = 180
    voting_seconds: int = 60
    revote_seconds: int = 45

    def to_json(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "slots": self.slots,
            "rounds": self.rounds,
            "timer_seconds": self.timer_seconds,
            "is_public": self.is_public,
            "explain_for_newbies": self.explain_for_newbies,
            "missing_vote_policy": self.missing_vote_policy.value,
            "content_pack_id": self.content_pack_id,
            "is_ranked": self.is_ranked,
            "room_kind": self.room_kind.value,
            "min_players": self.min_players,
            "bunker_seats": self.bunker_seats,
            "speech_seconds": self.speech_seconds,
            "discussion_seconds": self.discussion_seconds,
            "voting_seconds": self.voting_seconds,
            "revote_seconds": self.revote_seconds,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> "BunkerSettings":
        if not raw:
            return cls()

        raw_room_kind = raw.get("room_kind")
        if raw_room_kind is None:
            room_kind = RoomKind.RANKED if bool(raw.get("is_ranked", True)) else RoomKind.CASUAL
        else:
            room_kind = RoomKind(str(raw_room_kind))

        return cls(
            mode=GameMode(str(raw.get("mode", GameMode.CLASSIC.value))),
            slots=int(raw.get("slots", 8)),
            rounds=int(raw.get("rounds", 4)),
            timer_seconds=int(raw.get("timer_seconds", 180)),
            is_public=bool(raw.get("is_public", True)),
            explain_for_newbies=bool(raw.get("explain_for_newbies", True)),
            missing_vote_policy=VotePolicy(str(raw.get("missing_vote_policy", VotePolicy.ABSTAIN.value))),
            content_pack_id=int(raw["content_pack_id"]) if raw.get("content_pack_id") is not None else None,
            is_ranked=room_kind == RoomKind.RANKED,
            room_kind=room_kind,
            min_players=int(raw.get("min_players", 6)),
            bunker_seats=int(raw["bunker_seats"]) if raw.get("bunker_seats") is not None else None,
            speech_seconds=int(raw.get("speech_seconds", 60)),
            discussion_seconds=int(raw.get("discussion_seconds", raw.get("timer_seconds", 180))),
            voting_seconds=int(raw.get("voting_seconds", 60)),
            revote_seconds=int(raw.get("revote_seconds", 45)),
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
class BunkerGuildSettings:
    guild_id: int
    operator_role_id: int | None
    interest_role_id: int | None = None


@dataclass(frozen=True)
class BunkerContentPack:
    id: int
    guild_id: int
    name: str
    description: str
    content: dict[str, tuple[str, ...]]
    is_enabled: bool
    created_by: int
    updated_by: int | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class SpecialAbility:
    id: str
    name: str
    description: str
    effect: str
    target: str = "none"
    stat_key: str | None = None
    uses: int = 1
    timing: str = "any"
    revealed: bool = False
    used: bool = False
    blocked: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "effect": self.effect,
            "target": self.target,
            "stat_key": self.stat_key,
            "uses": self.uses,
            "timing": self.timing,
            "revealed": self.revealed,
            "used": self.used,
            "blocked": self.blocked,
        }

    @classmethod
    def from_json(cls, raw: Any) -> "SpecialAbility":
        if isinstance(raw, str):
            slug = raw.lower().replace(" ", "_")[:48] or "custom"
            return cls(
                id=slug,
                name=raw,
                description="Пользовательская возможность без отдельной логики применяет нейтральный эффект.",
                effect="generic_note",
            )
        if not isinstance(raw, dict):
            return cls(
                id="generic",
                name="Резервный протокол",
                description="Нейтральная спец. возможность.",
                effect="generic_note",
            )
        return cls(
            id=str(raw.get("id") or raw.get("name") or "generic")[:64],
            name=str(raw.get("name") or "Спец. возможность")[:80],
            description=str(raw.get("description") or raw.get("text") or "")[:300],
            effect=str(raw.get("effect") or "generic_note"),
            target=str(raw.get("target") or "none"),
            stat_key=str(raw["stat_key"]) if raw.get("stat_key") else None,
            uses=max(1, int(raw.get("uses", 1))),
            timing=str(raw.get("timing") or "any"),
            revealed=bool(raw.get("revealed", False)),
            used=bool(raw.get("used", False)),
            blocked=bool(raw.get("blocked", False)),
        )


@dataclass(frozen=True)
class CharacterCard:
    gender: str
    body: str
    age: str
    profession: str
    health: str
    skill: str
    phobia: str
    inventory: str
    fact: str
    special_abilities: tuple[SpecialAbility, ...] = field(default_factory=tuple)
    traitor: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "gender": self.gender,
            "body": self.body,
            "age": self.age,
            "profession": self.profession,
            "health": self.health,
            "skill": self.skill,
            "phobia": self.phobia,
            "inventory": self.inventory,
            "fact": self.fact,
            "special_abilities": [ability.to_json() for ability in self.special_abilities],
            "traitor": self.traitor,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> "CharacterCard | None":
        if not raw:
            return None

        abilities_raw = raw.get("special_abilities")
        if abilities_raw is None and raw.get("special_action"):
            abilities_raw = [raw.get("special_action")]
        if not isinstance(abilities_raw, (list, tuple)):
            abilities_raw = []

        return cls(
            gender=str(raw.get("gender", "не указано")),
            body=str(raw.get("body", raw.get("funny_trait", "среднее телосложение"))),
            age=str(raw.get("age", "")),
            profession=str(raw.get("profession", "")),
            health=str(raw.get("health", "")),
            skill=str(raw.get("skill", "")),
            phobia=str(raw.get("phobia", "")),
            inventory=str(raw.get("inventory", raw.get("item", ""))),
            fact=str(raw.get("fact", raw.get("secret", ""))),
            special_abilities=tuple(SpecialAbility.from_json(ability) for ability in abilities_raw)[:2],
            traitor=bool(raw.get("traitor", False)),
        )

    @property
    def item(self) -> str:
        return self.inventory

    @property
    def secret(self) -> str:
        return self.fact

    @property
    def funny_trait(self) -> str:
        return self.body

    @property
    def special_action(self) -> str:
        ability = self.special_abilities[0] if self.special_abilities else None
        return ability.name if ability else ""


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
    room_index: int = 0
    room_status: RoomStatus = RoomStatus.LOBBY
    is_admin_game: bool = False
    room_kind: RoomKind = RoomKind.RANKED
    public_message_ids: dict[str, int] = field(default_factory=dict)
    turn_order: tuple[int, ...] = field(default_factory=tuple)
    current_turn_index: int = 0
    reveals_done_this_turn: int = 0
    speech_index: int = 0
    collapsed_sections: dict[str, bool] = field(default_factory=dict)
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
    is_fake: bool = False
    final_revealed: bool = False

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
    confirmed_at: datetime | None = None
