from __future__ import annotations

import random
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Iterable

from siri_bot.bunker.content import BUILTIN_PACK, ContentPack
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
    Vote,
    VotePolicy,
)

MIN_PLAYERS = 6
MAX_PLAYERS = 16
FINAL_ALIVE_FLOOR = 2


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
    slots = max(MIN_PLAYERS, min(MAX_PLAYERS, settings.slots))
    rounds = max(3, min(6, settings.rounds or recommended_rounds(slots, settings.mode)))
    timer = max(30, min(900, settings.timer_seconds))
    if settings.mode == GameMode.TURBO:
        timer = max(30, timer // 2)

    return replace(settings, slots=slots, rounds=rounds, timer_seconds=timer)


def can_start_game(players: Iterable[BunkerPlayer]) -> tuple[bool, str]:
    active = [player for player in players if player.is_active]
    if len(active) < MIN_PLAYERS:
        return False, f"Нужно минимум {MIN_PLAYERS} игроков."

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
    return CharacterCard(
        profession=rng.choice(pack.professions),
        age=f"{age} лет",
        health=rng.choice(pack.weaknesses),
        skill=rng.choice(pack.skills),
        item=rng.choice(pack.items),
        phobia=rng.choice(pack.phobias),
        secret=rng.choice(pack.secrets),
        funny_trait=rng.choice(pack.funny_traits),
        special_action=rng.choice(pack.special_actions),
        traitor=traitor,
    )


def assign_cards(players: list[BunkerPlayer], settings: BunkerSettings, rng: random.Random | None = None) -> dict[int, CharacterCard]:
    rng = rng or random.Random()
    traitor_id = rng.choice([player.user_id for player in players]) if settings.mode == GameMode.TRAITOR and players else None
    return {
        player.user_id: generate_card(rng, traitor=player.user_id == traitor_id)
        for player in players
        if player.is_active
    }


def selectable_reveal_stats(player: BunkerPlayer) -> list[str]:
    revealed = set(player.revealed_stats)
    return [stat for stat in REVEALABLE_STATS if stat not in revealed]


def reveal_stat(player: BunkerPlayer, stat: str) -> tuple[bool, str]:
    if stat not in REVEALABLE_STATS:
        return False, "Эту характеристику нельзя раскрыть через обычный reveal."

    if stat in player.revealed_stats:
        return False, "Эта характеристика уже раскрыта."

    if player.card is None:
        return False, "Карточка еще не выдана."

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
    targets: list[int] = []
    abstains = 0
    for voter_id in alive_ids:
        vote = vote_by_voter.get(voter_id)
        if vote is None:
            if policy == VotePolicy.RANDOM:
                targets.append(rng.choice([target for target in alive_ids if target != voter_id] or alive_ids))
            else:
                abstains += 1
            continue

        if vote.is_abstain or vote.target_user_id is None:
            abstains += 1
        elif vote.target_user_id in alive_ids:
            targets.append(vote.target_user_id)

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
    target = max(FINAL_ALIVE_FLOOR, game.settings.slots // 2)
    return game.round_number >= game.settings.rounds or alive_count <= target


def next_state_after_timer(state: GameState) -> GameState:
    transitions = {
        GameState.REVEAL_PHASE: GameState.DISCUSSION_PHASE,
        GameState.DISCUSSION_PHASE: GameState.CHAOS_PHASE,
        GameState.CHAOS_PHASE: GameState.VOTING_PHASE,
        GameState.VOTING_PHASE: GameState.ELIMINATION_PHASE,
        GameState.ELIMINATION_PHASE: GameState.REVEAL_PHASE,
    }
    return transitions.get(state, state)


def phase_deadline(settings: BunkerSettings, state: GameState, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(UTC)
    if state in {GameState.LOBBY, GameState.PREPARING, GameState.FINAL_PHASE, GameState.FINISHED}:
        return None

    multiplier = {
        GameState.REVEAL_PHASE: 1.0,
        GameState.DISCUSSION_PHASE: 1.5,
        GameState.CHAOS_PHASE: 0.75,
        GameState.VOTING_PHASE: 1.0,
        GameState.ELIMINATION_PHASE: 0.5,
    }.get(state, 1.0)
    return now + timedelta(seconds=max(30, int(settings.timer_seconds * multiplier)))


def final_epilogue(game: BunkerGame, players: list[BunkerPlayer], rng: random.Random | None = None) -> str:
    rng = rng or random.Random()
    alive = [player for player in players if player.is_alive]
    eliminated = [player for player in players if player.is_eliminated]
    names = ", ".join(player.display_name for player in alive) or "никто"
    leader = rng.choice(alive).display_name if alive else "пустой стул"
    mvp = max(alive, key=lambda player: len(player.revealed_stats), default=None)
    lovable = rng.choice(eliminated or alive).display_name if players else "неизвестный герой"
    base_score = 45 + len(alive) * 7
    if game.profile:
        base_score += (game.profile.resources.food + game.profile.resources.water + game.profile.resources.electricity + game.profile.resources.morale) // 20
        base_score -= game.profile.resources.radiation // 3
    if any(player.card and player.card.traitor and player.is_alive for player in alive):
        base_score -= 18
    survival = max(1, min(99, base_score))

    return (
        f"Выжили: {names}.\n"
        f"Первый год прошел под девизом: '{rng.choice(('не трогай генератор', 'сначала совет, потом паника', 'кто съел пайки'))}'.\n"
        f"Лидером стал(а): {leader}.\n"
        f"Важную систему сломал(а): {lovable}, но все сделали вид, что так и было.\n"
        f"MVP: {mvp.display_name if mvp else 'не назначен'}.\n"
        f"Самая бесполезная, но любимая единица: {lovable}.\n"
        f"Итоговый шанс выживания бункера: {survival}%.\n"
        "Мемная концовка: бункер выжил, но спор о майонезе теперь внесен в конституцию."
    )


def format_card(card: CharacterCard) -> str:
    lines = [
        f"Профессия: {card.profession}",
        f"Возраст: {card.age}",
        f"Здоровье: {card.health}",
        f"Навык: {card.skill}",
        f"Предмет: {card.item}",
        f"Фобия: {card.phobia}",
        f"Секрет: {card.secret}",
        f"Черта: {card.funny_trait}",
        f"Спец-действие: {card.special_action}",
    ]
    if card.traitor:
        lines.append("Скрытая роль: предатель. Доживи до финала и испорть статистику.")

    return "\n".join(lines)

