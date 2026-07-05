from __future__ import annotations

import random
import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Iterable

from siri_bot.bunker.content import BODY_TYPES, BUILTIN_PACK, ContentPack, GENDERS
from siri_bot.bunker.models import (
    BunkerGame,
    BunkerPlayer,
    BunkerProfile,
    BunkerResources,
    BunkerSettings,
    CARD_STAT_LABELS,
    CharacterCard,
    GameMode,
    GameState,
    REVEALABLE_STATS,
    RoomKind,
    SpecialAbility,
    Vote,
    VotePolicy,
    normalize_card_stat_key,
)

MIN_PLAYERS = 6
MAX_PLAYERS = 16
FINAL_ALIVE_FLOOR = 2

BIOLOGY_TRAITS: tuple[str, ...] = (
    "может иметь детей, хронических ограничений не выявлено",
    "репродуктивная функция снижена, но общее состояние стабильное",
    "имеет медицинское ограничение по репродукции",
    "генетических рисков по базовому скринингу не обнаружено",
    "требуется дополнительное обследование репродуктивного здоровья",
    "репродуктивный статус неизвестен",
)

ROUND_REVEAL_STATS: tuple[tuple[str, ...], ...] = (
    ("profession",),
    ("health", "age"),
    ("hobby",),
    ("baggage",),
    ("phobia", "character_trait"),
    ("extra_fact",),
    ("gender", "biology"),
)


def recommended_rounds(slots: int, mode: GameMode = GameMode.CLASSIC) -> int:
    if slots <= 8:
        rounds = 4
    elif slots <= 12:
        rounds = 5
    else:
        rounds = 6

    if mode == GameMode.TURBO:
        return max(3, rounds - 1)

    return rounds


def normalize_settings(settings: BunkerSettings) -> BunkerSettings:
    room_kind = RoomKind.ADMIN_TEST if settings.room_kind == RoomKind.ADMIN_TEST else RoomKind.RANKED
    is_ranked = room_kind == RoomKind.RANKED
    slots = max(MIN_PLAYERS, min(MAX_PLAYERS, settings.slots))
    min_players = max(MIN_PLAYERS, min(slots, settings.min_players))
    if room_kind == RoomKind.ADMIN_TEST:
        min_players = 1
    bunker_seats = settings.bunker_seats
    if bunker_seats is not None:
        bunker_seats = max(1, min(slots - 1, bunker_seats))
    rounds = max(3, min(7, settings.rounds or recommended_rounds(slots, settings.mode)))
    timer = max(30, min(900, settings.timer_seconds))
    if settings.mode == GameMode.TURBO:
        timer = max(30, timer // 2)

    return replace(
        settings,
        slots=slots,
        rounds=rounds,
        timer_seconds=timer,
        min_players=min_players,
        bunker_seats=bunker_seats,
        is_ranked=is_ranked,
        is_public=False if room_kind == RoomKind.ADMIN_TEST else settings.is_public,
        room_kind=room_kind,
        speech_seconds=max(15, min(300, settings.speech_seconds)),
        discussion_seconds=max(30, min(900, settings.discussion_seconds)),
        voting_seconds=max(15, min(300, settings.voting_seconds)),
        revote_seconds=max(15, min(180, settings.revote_seconds)),
    )


def can_start_game(players: Iterable[BunkerPlayer], *, min_players: int = MIN_PLAYERS, ranked: bool = False) -> tuple[bool, str]:
    active = [player for player in players if player.is_active]
    if len(active) < min_players:
        return False, f"Нужно минимум {min_players} игроков."

    if ranked:
        real_players = [player for player in active if not player.is_fake]
        if len(real_players) < min_players:
            return False, "Ranked не стартует с тест-ботами или недостатком реальных игроков."

    waiting = [player.display_name for player in active if not player.is_host and player.ready_at is None]
    if waiting:
        return False, "Не все игроки нажали 'Готов': " + ", ".join(waiting[:8])

    return True, "Готово к старту."


def generate_profile(settings: BunkerSettings, rng: random.Random | None = None, pack: ContentPack = BUILTIN_PACK) -> BunkerProfile:
    rng = rng or random.Random()
    base = 60 if settings.mode == GameMode.HARDCORE else 70
    radiation = 35 if settings.mode == GameMode.HARDCORE else 20
    profile = BunkerProfile(
        apocalypse=rng.choice(pack.apocalypses),
        layout=rng.choice(pack.layouts),
        defect=rng.choice(pack.bunker_defects),
        resources=BunkerResources(
            food=base + rng.randint(-10, 10),
            water=base + rng.randint(-10, 10),
            electricity=base + rng.randint(-15, 10),
            morale=base + rng.randint(-10, 15),
            radiation=radiation + rng.randint(-10, 10),
        ).clamp(),
    )
    return profile


def generate_card(
    rng: random.Random | None = None,
    pack: ContentPack = BUILTIN_PACK,
    *,
    traitor: bool = False,
) -> CharacterCard:
    rng = rng or random.Random()
    age = rng.randint(18, 78)
    abilities = _pick_special_abilities(rng, pack)
    return CharacterCard(
        profession=rng.choice(pack.professions),
        age=f"{age} лет",
        gender=rng.choice(GENDERS),
        health=rng.choice(pack.weaknesses),
        phobia=rng.choice(pack.phobias),
        hobby=rng.choice(pack.skills),
        baggage=rng.choice(pack.items),
        extra_fact=rng.choice(pack.secrets),
        character_trait=rng.choice(pack.funny_traits or BODY_TYPES),
        biology=rng.choice(BIOLOGY_TRAITS),
        special_abilities=abilities,
        traitor=traitor,
    )


def _pick_special_abilities(rng: random.Random, pack: ContentPack) -> tuple[SpecialAbility, SpecialAbility]:
    raw_values = list(pack.special_actions)
    rng.shuffle(raw_values)
    abilities: list[SpecialAbility] = []
    seen: set[str] = set()
    for raw in raw_values:
        ability = _parse_special_ability(raw)
        if ability.id in seen:
            continue
        seen.add(ability.id)
        abilities.append(ability)
        if len(abilities) == 2:
            break

    while len(abilities) < 2:
        abilities.append(
            SpecialAbility(
                id=f"reserve_{len(abilities) + 1}",
                name="Резервный протокол",
                description="Нейтральная возможность без активного эффекта.",
                effect="generic_note",
            )
        )
    return tuple(abilities[:2])  # type: ignore[return-value]


def _parse_special_ability(raw: str) -> SpecialAbility:
    text = str(raw).strip()
    if text.startswith("{"):
        try:
            return SpecialAbility.from_json(json.loads(text))
        except (json.JSONDecodeError, TypeError, ValueError):
            return SpecialAbility.from_json(text)
    return SpecialAbility.from_json(text)


def assign_cards(
    players: list[BunkerPlayer],
    settings: BunkerSettings,
    rng: random.Random | None = None,
    pack: ContentPack = BUILTIN_PACK,
) -> dict[int, CharacterCard]:
    rng = rng or random.Random()
    traitor_id = rng.choice([player.user_id for player in players]) if settings.mode == GameMode.TRAITOR and players else None
    return {
        player.user_id: generate_card(rng, pack, traitor=player.user_id == traitor_id)
        for player in players
        if player.is_active
    }


def selectable_reveal_stats(player: BunkerPlayer) -> list[str]:
    revealed = set(player.revealed_stats)
    return [stat for stat in REVEALABLE_STATS if stat not in revealed]


def required_stats_for_round(round_number: int) -> tuple[str, ...]:
    if round_number <= 0:
        return ROUND_REVEAL_STATS[0]
    index = round_number - 1
    if index < len(ROUND_REVEAL_STATS):
        return ROUND_REVEAL_STATS[index]
    return tuple(stat for stat in REVEALABLE_STATS if stat not in {item for group in ROUND_REVEAL_STATS for item in group})


def revealable_stats_for_round(player: BunkerPlayer, round_number: int) -> list[str]:
    revealed = set(player.revealed_stats)
    return [stat for stat in required_stats_for_round(round_number) if stat not in revealed]


def player_completed_round_reveal(player: BunkerPlayer, round_number: int) -> bool:
    required = required_stats_for_round(round_number)
    if not required:
        return True
    revealed = set(player.revealed_stats)
    return all(stat in revealed for stat in required)


def next_reveal_stat(player: BunkerPlayer, round_number: int | None = None) -> str | None:
    stats = revealable_stats_for_round(player, round_number) if round_number is not None else selectable_reveal_stats(player)
    return stats[0] if stats else None


def reveal_stat(player: BunkerPlayer, stat: str, *, round_number: int | None = None) -> tuple[bool, str]:
    stat = normalize_card_stat_key(stat) or ""
    if stat not in REVEALABLE_STATS:
        return False, "Эту характеристику нельзя раскрыть через обычный reveal."

    if stat in player.revealed_stats:
        return False, "Эта характеристика уже раскрыта."

    if player.card is None:
        return False, "Карточка еще не выдана."

    if round_number is not None:
        required = required_stats_for_round(round_number)
        if stat not in required:
            labels = ", ".join(CARD_STAT_LABELS[item] for item in required) or "нет обязательных характеристик"
            return False, f"В этом раунде открываются: {labels}."
    else:
        expected = next_reveal_stat(player)
        if expected is not None and stat != expected:
            return False, f"Сначала нужно раскрыть: {CARD_STAT_LABELS[expected]}."

    return True, f"{player.display_name} раскрывает: {CARD_STAT_LABELS[stat]} - {getattr(player.card, stat)}"


def pick_chaos_event(rng: random.Random | None = None, pack: ContentPack = BUILTIN_PACK) -> str:
    rng = rng or random.Random()
    return rng.choice(pack.chaos_events)


def apply_chaos_to_resources(resources: BunkerResources, rng: random.Random | None = None) -> BunkerResources:
    rng = rng or random.Random()
    return BunkerResources(
        food=resources.food + rng.randint(-7, 5),
        water=resources.water + rng.randint(-7, 5),
        electricity=resources.electricity + rng.randint(-9, 6),
        morale=resources.morale + rng.randint(-8, 8),
        radiation=resources.radiation + rng.randint(-4, 6),
    ).clamp()


def tally_votes(
    players: list[BunkerPlayer],
    votes: list[Vote],
    policy: VotePolicy,
    rng: random.Random | None = None,
) -> tuple[int | None, str]:
    rng = rng or random.Random()
    alive_ids = [player.user_id for player in players if player.is_alive]
    if not alive_ids:
        return None, "В бункере не осталось активных игроков."

    vote_by_voter = {vote.voter_id: vote for vote in votes}
    player_by_id = {player.user_id: player for player in players}
    targets: list[int] = []
    abstains = 0
    for voter_id in alive_ids:
        voter = player_by_id.get(voter_id)
        vote_weight = max(0, 1 + (voter.personal_bonus if voter else 0))
        if vote_weight <= 0:
            continue
        vote = vote_by_voter.get(voter_id)
        if vote is None:
            if policy == VotePolicy.RANDOM:
                targets.extend([rng.choice(alive_ids)] * vote_weight)
            else:
                abstains += 1
            continue

        if vote.is_abstain or vote.target_user_id is None:
            abstains += 1
        elif vote.target_user_id in alive_ids:
            targets.extend([vote.target_user_id] * vote_weight)

    if not targets:
        return None, f"Никого не выгнали: все воздержались ({abstains})."

    counts = Counter(targets)
    top_count = max(counts.values())
    tied = [user_id for user_id, count in counts.items() if count == top_count]
    eliminated = rng.choice(tied)
    if len(tied) > 1:
        return eliminated, f"Ничья по {top_count} голосам. Судьба выбрала <@{eliminated}>."

    return eliminated, f"<@{eliminated}> получает {top_count} голосов и покидает бункер."


def should_enter_final(game: BunkerGame, players: list[BunkerPlayer]) -> bool:
    alive_count = sum(1 for player in players if player.is_alive)
    target = max(FINAL_ALIVE_FLOOR, game.settings.bunker_seats or game.settings.slots // 2)
    return game.round_number >= game.settings.rounds or alive_count <= target


def next_state_after_timer(state: GameState) -> GameState:
    transitions = {
        GameState.SPEECH_PHASE: GameState.DISCUSSION_PHASE,
        GameState.DISCUSSION_PHASE: GameState.CHAOS_PHASE,
        GameState.CHAOS_PHASE: GameState.VOTING_PHASE,
        GameState.VOTING_PHASE: GameState.ELIMINATION_PHASE,
        GameState.ELIMINATION_PHASE: GameState.REVEAL_PHASE,
    }
    return transitions.get(state, state)


def phase_deadline(settings: BunkerSettings, state: GameState, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(UTC)
    if state in {GameState.LOBBY, GameState.PREPARING, GameState.REVEAL_PHASE, GameState.FINAL_PHASE, GameState.FINISHED}:
        return None

    seconds_by_state = {
        GameState.SPEECH_PHASE: settings.speech_seconds,
        GameState.DISCUSSION_PHASE: settings.discussion_seconds,
        GameState.CHAOS_PHASE: max(30, settings.timer_seconds // 2),
        GameState.VOTING_PHASE: settings.voting_seconds,
        GameState.ELIMINATION_PHASE: settings.revote_seconds,
    }
    return now + timedelta(seconds=max(15, int(seconds_by_state.get(state, settings.timer_seconds))))


def final_epilogue(game: BunkerGame, players: list[BunkerPlayer], rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    alive = [player for player in players if player.is_alive]
    eliminated = [player for player in players if player.is_eliminated]
    names = ", ".join(player.display_name for player in alive) or "никто"
    leader = rng.choice(alive).display_name if alive else "не назначен"
    mvp = max(alive, key=lambda player: len(player.revealed_stats), default=None)
    base_score = 45 + len(alive) * 7
    if game.profile:
        base_score += (game.profile.resources.food + game.profile.resources.water + game.profile.resources.electricity + game.profile.resources.morale) // 20
        base_score -= game.profile.resources.radiation // 3
    if any(player.card and player.card.traitor and player.is_alive for player in alive):
        base_score -= 18
    survival = max(1, min(99, base_score))

    return (
        f"Выжили: {names}.\n"
        f"Координатором первого цикла стал(а): {leader}.\n"
        f"MVP: {mvp.display_name if mvp else 'не назначен'}.\n"
        f"Выгнано до финала: {len(eliminated)}.\n"
        f"Итоговый шанс выживания бункера: {survival}%.\n"
        "Финальный протокол закрыт. Дальнейшее выживание зависит от дисциплины, распределения ресурсов и состояния систем."
    )


def format_card(card: CharacterCard) -> str:
    lines = [
        f"Профессия: {card.profession}",
        f"Возраст: {card.age}",
        f"Пол: {card.gender}",
        f"Здоровье: {card.health}",
        f"Фобия: {card.phobia}",
        f"Хобби/навык: {card.hobby}",
        f"Багаж: {card.baggage}",
        f"Доп. факт: {card.extra_fact}",
        f"Черта характера: {card.character_trait}",
        f"Биологическая характеристика: {card.biology}",
        "Спец. возможности:",
        *[f"- {ability.name}: {ability.description or ability.effect}" for ability in card.special_abilities],
    ]
    if card.traitor:
        lines.append("Скрытая роль: предатель. Доживи до финала и испорть статистику.")

    return "\n".join(lines)
