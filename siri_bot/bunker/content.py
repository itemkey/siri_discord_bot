from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

KNOWN_ABILITY_EFFECTS: frozenset[str] = frozenset(
    {
        "steal_stat",
        "swap_stat",
        "reroll_stat",
        "exile_immunity",
        "second_vote",
        "block_vote",
        "reveal_other_stat",
        "protect_action",
        "generic_note",
    }
)

GENDERS: tuple[str, ...] = ("женщина", "мужчина", "небинарный человек")

BODY_TYPES: tuple[str, ...] = (
    "выносливое телосложение",
    "среднее телосложение",
    "крепкое телосложение",
    "хрупкое телосложение",
    "атлетичное телосложение",
    "низкая выносливость",
)

BUILTIN_SPECIAL_ABILITIES: tuple[dict[str, Any], ...] = (
    {
        "id": "steal_profession",
        "name": "Перехват роли",
        "description": "Один раз украсть раскрытую или скрытую профессию цели.",
        "effect": "steal_stat",
        "target": "alive_other",
        "stat_key": "profession",
        "uses": 1,
        "timing": "before_vote",
    },
    {
        "id": "swap_inventory",
        "name": "Обмен запасами",
        "description": "Один раз обменяться инвентарем с выбранным живым игроком.",
        "effect": "swap_stat",
        "target": "alive_other",
        "stat_key": "inventory",
        "uses": 1,
        "timing": "any",
    },
    {
        "id": "reroll_health",
        "name": "Медицинский пересмотр",
        "description": "Один раз заменить свое состояние здоровья новым значением из пака.",
        "effect": "reroll_stat",
        "target": "self",
        "stat_key": "health",
        "uses": 1,
        "timing": "reveal_or_discussion",
    },
    {
        "id": "exile_immunity",
        "name": "Протокол неприкосновенности",
        "description": "Один раз защититься от изгнания в текущем раунде.",
        "effect": "exile_immunity",
        "target": "self",
        "uses": 1,
        "timing": "before_vote",
    },
    {
        "id": "second_vote",
        "name": "Решающая бюллетень",
        "description": "Голос этого игрока в текущем голосовании считается как два.",
        "effect": "second_vote",
        "target": "self",
        "uses": 1,
        "timing": "voting",
    },
    {
        "id": "block_vote",
        "name": "Процедурная блокировка",
        "description": "Один раз лишить выбранного живого игрока голоса в текущем раунде.",
        "effect": "block_vote",
        "target": "alive_other",
        "uses": 1,
        "timing": "before_vote",
    },
    {
        "id": "reveal_other_skill",
        "name": "Запрос сведений",
        "description": "Один раз раскрыть навык выбранного живого игрока.",
        "effect": "reveal_other_stat",
        "target": "alive_other",
        "stat_key": "skill",
        "uses": 1,
        "timing": "reveal_or_discussion",
    },
    {
        "id": "protect_action",
        "name": "Контрмера",
        "description": "Один раз заблокировать спец. действие, направленное на себя.",
        "effect": "protect_action",
        "target": "self",
        "uses": 1,
        "timing": "any",
    },
)

SPECIAL_ACTIONS: tuple[str, ...] = tuple(
    json.dumps(ability, ensure_ascii=False, sort_keys=True) for ability in BUILTIN_SPECIAL_ABILITIES
)

PACK_FIELDS: tuple[str, ...] = (
    "professions",
    "items",
    "weaknesses",
    "secrets",
    "skills",
    "phobias",
    "funny_traits",
    "apocalypses",
    "bunker_defects",
    "chaos_events",
    "layouts",
    "special_actions",
)

PACK_FIELD_LABELS: dict[str, str] = {
    "professions": "Профессии",
    "items": "Багаж",
    "weaknesses": "Здоровье",
    "secrets": "Доп. факты",
    "skills": "Хобби/навыки",
    "phobias": "Фобии",
    "funny_traits": "Черты характера",
    "apocalypses": "Катаклизмы",
    "bunker_defects": "Состояние бункера",
    "chaos_events": "События",
    "layouts": "Планировки",
    "special_actions": "Спец. возможности",
}


@dataclass(frozen=True)
class ContentPack:
    professions: tuple[str, ...]
    items: tuple[str, ...]
    weaknesses: tuple[str, ...]
    secrets: tuple[str, ...]
    skills: tuple[str, ...]
    phobias: tuple[str, ...]
    funny_traits: tuple[str, ...]
    apocalypses: tuple[str, ...]
    bunker_defects: tuple[str, ...]
    chaos_events: tuple[str, ...]
    layouts: tuple[str, ...]
    special_actions: tuple[str, ...] = SPECIAL_ACTIONS

    def counts(self) -> dict[str, int]:
        return {field: len(getattr(self, field)) for field in PACK_FIELDS}

    def to_json(self) -> dict[str, list[str]]:
        return {field: list(getattr(self, field)) for field in PACK_FIELDS}

    @classmethod
    def from_json(cls, raw: dict[str, Any] | None) -> "ContentPack":
        content = normalize_pack_content(raw or {})
        return cls(**content)


def empty_content_pack() -> ContentPack:
    return ContentPack(**{field: () for field in PACK_FIELDS})


def merge_content_packs(base: ContentPack, extra: ContentPack | None) -> ContentPack:
    if extra is None:
        return base

    values: dict[str, tuple[str, ...]] = {}
    for field in PACK_FIELDS:
        merged: list[str] = []
        seen: set[str] = set()
        for value in (*getattr(base, field), *getattr(extra, field)):
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            merged.append(value)
        values[field] = tuple(merged)

    return ContentPack(**values)


def normalize_pack_content(raw: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    unknown = sorted(set(raw) - set(PACK_FIELDS))
    if unknown:
        raise ValueError("Неизвестные категории пака: " + ", ".join(unknown))

    content: dict[str, tuple[str, ...]] = {}
    for field in PACK_FIELDS:
        raw_values = raw.get(field, ())
        if raw_values is None:
            raw_values = ()
        if not isinstance(raw_values, (list, tuple)):
            raise ValueError(f"Категория {field} должна быть списком строк.")

        values: list[str] = []
        seen: set[str] = set()
        for value in raw_values:
            text = str(value).strip()
            if field == "special_actions":
                text = _normalize_ability_payload(value)
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(text[:600] if field == "special_actions" else text[:180])
        content[field] = tuple(values)

    return content


def _normalize_ability_payload(value: Any) -> str:
    if isinstance(value, dict):
        effect = str(value.get("effect") or "generic_note")
        if effect not in KNOWN_ABILITY_EFFECTS:
            raise ValueError(f"Неизвестный effect спец. возможности: {effect}")
        payload = {
            "id": str(value.get("id") or value.get("name") or "custom")[:64],
            "name": str(value.get("name") or "Спец. возможность")[:80],
            "description": str(value.get("description") or "")[:300],
            "effect": effect,
            "target": str(value.get("target") or "none")[:32],
            "stat_key": value.get("stat_key"),
            "uses": max(1, int(value.get("uses", 1))),
            "timing": str(value.get("timing") or "any")[:32],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    text = str(value).strip()
    if not text:
        return ""
    if text.startswith("{"):
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("Спец. возможность в JSON должна быть объектом.")
        return _normalize_ability_payload(parsed)
    return text[:180]


CORE_PROFESSIONS = (
    "инженер систем жизнеобеспечения",
    "врач неотложной помощи",
    "агроном закрытых теплиц",
    "электромеханик",
    "специалист по водоочистке",
    "психолог кризисных групп",
    "повар длительного хранения",
    "радиотехник",
    "строитель укреплений",
    "логист запасов",
    "фельдшер",
    "биолог-микробиолог",
)

CORE_ITEMS = (
    "переносной фильтр воды",
    "набор стерильных инструментов",
    "карта подземных коммуникаций",
    "комплект семян овощей",
    "мультитул",
    "ручной радиоприемник",
    "аптечка первой помощи",
    "теплая рабочая одежда",
    "фонарь с запасными аккумуляторами",
    "портативная солнечная панель",
)

CORE_HEALTH = (
    "здоров, высокая выносливость",
    "астма легкой степени",
    "хроническая мигрень",
    "перенесенная травма колена",
    "аллергия на пыльцу",
    "стабильное состояние после операции",
    "сниженное зрение без очков",
    "редкая группа крови",
    "устойчивость к стрессу выше средней",
    "ослабленный иммунитет",
)

CORE_FACTS = (
    "имеет опыт жизни в изоляции",
    "знает расположение ближайшего склада медикаментов",
    "проходил курсы гражданской обороны",
    "может вести учет пайков без ошибок",
    "умеет обучать новичков базовым процедурам",
    "знает несколько языков",
    "имеет опыт переговоров в кризисах",
    "работал на объекте с повышенной секретностью",
    "поддерживает подробный личный журнал наблюдений",
    "умеет сохранять спокойствие при авариях",
)

CORE_SKILLS = (
    "ремонт вентиляции",
    "первая медицинская помощь",
    "выращивание растений без почвы",
    "распределение запасов",
    "электромонтаж",
    "очистка воды",
    "медиация конфликтов",
    "ориентирование по техническим схемам",
    "приготовление пищи из ограниченных запасов",
    "организация смен и дежурств",
)

CORE_PHOBIAS = (
    "клаустрофобия слабой степени",
    "страх полной темноты",
    "страх заражения",
    "страх громких аварийных сигналов",
    "страх открытой воды",
    "страх высоты",
    "страх медицинских процедур",
    "страх одиночества",
    "страх огня",
    "страх замкнутых лифтов",
)

CORE_CHARACTER_TRAITS = (
    "спокойный и дисциплинированный",
    "склонен брать ответственность",
    "быстро конфликтует под давлением",
    "хорошо работает в группе",
    "замкнутый, но надежный",
    "легко поддается панике",
    "умеет договариваться в споре",
    "строго соблюдает правила",
    "склонен скрывать проблемы",
    "выдерживает долгую изоляцию",
)

CORE_APOCALYPSES = (
    "Серия ядерных ударов разрушила крупные города. Поверхность заражена, связь нестабильна, ближайшие месяцы решают судьбу выживших.",
    "Пандемия неизвестного патогена вызвала распад систем здравоохранения. Без изоляции и контроля ресурсов группа не продержится.",
    "Глобальная климатическая катастрофа сделала регион непригодным для жизни. Бункер остается единственным стабильным убежищем.",
    "Солнечная буря вывела из строя энергосети и спутниковую связь. На поверхности растет насилие из-за нехватки воды и лекарств.",
    "Химическая авария континентального масштаба отравила воздух и водоемы. Выход наружу возможен только после длительной очистки.",
)

CORE_DEFECTS = (
    "часть вентиляции требует постоянного обслуживания",
    "резервный генератор нестабилен при высокой нагрузке",
    "медблок укомплектован наполовину",
    "склад воды защищен, но насос изношен",
    "один жилой отсек поврежден и требует ремонта",
    "радиосвязь работает только короткими окнами",
    "теплица запущена, но нуждается в специалисте",
)

CORE_EVENTS = (
    "Фильтры вентиляции показали перегрузку. До следующего раунда система работает в экономном режиме.",
    "Обнаружена протечка в техническом отсеке. Нужны люди с инженерными или ремонтными навыками.",
    "Медицинский журнал выявил риск инфекции. Игроки должны учитывать состояние здоровья команды.",
    "Складские весы показали расхождение в запасах. Рацион на ближайший цикл пересматривается.",
    "Радиосвязь поймала слабый сигнал другой группы. Решение о контакте откладывается до голосования.",
    "Теплица дала первый урожай, но требует ответственного ухода.",
)

CORE_LAYOUTS = (
    "два жилых отсека, медблок, кухня, склад, вентиляционная станция и технический коридор",
    "три уровня: жилой, производственный и инженерный; между уровнями один основной лифт и аварийная лестница",
    "компактный гражданский бункер с теплицей, пунктом связи, складом воды и малым медблоком",
    "старый военный объект с укрепленным входом, мастерской, радиорубкой и отдельным карантинным отсеком",
    "исследовательский комплекс с лабораторией, автономной энергетикой, складом семян и наблюдательным постом",
)


def _extend(seed: tuple[str, ...], target: int, pattern: str) -> tuple[str, ...]:
    values = list(seed)
    index = 1
    while len(values) < target:
        values.append(pattern.format(index=index, base=seed[(index - 1) % len(seed)]))
        index += 1
    return tuple(values)


BUILTIN_PACK = ContentPack(
    professions=CORE_PROFESSIONS,
    items=CORE_ITEMS,
    weaknesses=CORE_HEALTH,
    secrets=CORE_FACTS,
    skills=CORE_SKILLS,
    phobias=CORE_PHOBIAS,
    funny_traits=CORE_CHARACTER_TRAITS,
    apocalypses=CORE_APOCALYPSES,
    bunker_defects=CORE_DEFECTS,
    chaos_events=CORE_EVENTS,
    layouts=CORE_LAYOUTS,
)
