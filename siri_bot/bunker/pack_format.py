from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from siri_bot.bunker.content import KNOWN_ABILITY_EFFECTS, PACK_FIELD_LABELS, PACK_FIELDS, normalize_pack_content

# Public contract for external bunker-pack editors.
# Editors should generate UTF-8 JSON with this format/version pair and the
# content categories from PACK_FILE_EDITOR_HINTS["categories"].
PACK_FILE_FORMAT = "siri-bunker-pack"
PACK_FILE_VERSION = 1
PACK_FILE_EXTENSION = ".bunker-pack.json"
PACK_FILE_MAX_BYTES = 512 * 1024
PACK_TEXT_VALUE_MAX_CHARS = 180
PACK_SPECIAL_ACTION_MAX_CHARS = 600
PACK_NAME_MAX_CHARS = 80
PACK_DESCRIPTION_MAX_CHARS = 500

PACK_FILE_EDITOR_HINTS: dict[str, Any] = {
    "format": PACK_FILE_FORMAT,
    "version": PACK_FILE_VERSION,
    "extension": PACK_FILE_EXTENSION,
    "encoding": "utf-8",
    "max_file_bytes": PACK_FILE_MAX_BYTES,
    "name_max_chars": PACK_NAME_MAX_CHARS,
    "description_max_chars": PACK_DESCRIPTION_MAX_CHARS,
    "text_value_max_chars": PACK_TEXT_VALUE_MAX_CHARS,
    "special_action_max_chars": PACK_SPECIAL_ACTION_MAX_CHARS,
    "categories": [
        {
            "key": field,
            "label": PACK_FIELD_LABELS[field],
            "value_type": "string_or_object" if field == "special_actions" else "string",
            "max_chars": PACK_SPECIAL_ACTION_MAX_CHARS if field == "special_actions" else PACK_TEXT_VALUE_MAX_CHARS,
        }
        for field in PACK_FIELDS
    ],
    "special_action": {
        "string_behavior": "A plain string becomes a neutral generic_note ability.",
        "object_fields": ("id", "name", "description", "effect", "target", "stat_key", "uses", "timing", "actions"),
        "action_fields": ("effect", "target", "stat_key"),
        "known_effects": tuple(sorted(KNOWN_ABILITY_EFFECTS)),
    },
}


@dataclass(frozen=True)
class ParsedPackFile:
    name: str | None
    description: str | None
    content: dict[str, tuple[str, ...]]


def parse_pack_file(raw: bytes | str) -> ParsedPackFile:
    """Parse editor JSON, legacy {name, description, content}, or raw category JSON."""
    text = _decode_pack_file(raw)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Некорректный JSON: {exc.msg} (строка {exc.lineno}, колонка {exc.colno}).") from exc

    if not isinstance(payload, dict):
        raise ValueError("JSON пака должен быть объектом.")

    if "format" in payload or "version" in payload:
        content_source = _content_from_versioned_payload(payload)
    else:
        content_source = payload.get("content", payload)
        if not isinstance(content_source, dict):
            raise ValueError("Поле content должно быть объектом.")

    name = _optional_text(payload, "name", PACK_NAME_MAX_CHARS)
    description = _optional_text(payload, "description", PACK_DESCRIPTION_MAX_CHARS)
    return ParsedPackFile(name=name, description=description, content=normalize_pack_content(content_source))


def dump_pack_file(*, name: str, description: str = "", content: dict[str, Any]) -> str:
    return json.dumps(pack_file_payload(name=name, description=description, content=content), ensure_ascii=False, indent=2) + "\n"


def pack_file_payload(*, name: str, description: str = "", content: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_pack_content(content)
    return {
        "format": PACK_FILE_FORMAT,
        "version": PACK_FILE_VERSION,
        "name": str(name).strip()[:PACK_NAME_MAX_CHARS],
        "description": str(description).strip()[:PACK_DESCRIPTION_MAX_CHARS],
        "content": {
            field: [_special_action_for_file(value) for value in normalized[field]]
            if field == "special_actions"
            else list(normalized[field])
            for field in PACK_FIELDS
        },
    }


def pack_file_template() -> str:
    return dump_pack_file(
        name="Новый пак Бункера",
        description="Описание пака для редактора",
        content={field: () for field in PACK_FIELDS},
    )


def _decode_pack_file(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        if len(raw) > PACK_FILE_MAX_BYTES:
            raise ValueError(f"Файл пака слишком большой: максимум {PACK_FILE_MAX_BYTES // 1024} КБ.")
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Файл пака должен быть UTF-8 JSON.") from exc

    text = str(raw)
    if len(text.encode("utf-8")) > PACK_FILE_MAX_BYTES:
        raise ValueError(f"JSON пака слишком большой: максимум {PACK_FILE_MAX_BYTES // 1024} КБ.")
    return text


def _content_from_versioned_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("format") != PACK_FILE_FORMAT:
        raise ValueError(f"Неизвестный формат пака: {payload.get('format')!r}.")
    if payload.get("version") != PACK_FILE_VERSION:
        raise ValueError(f"Неподдерживаемая версия пака: {payload.get('version')!r}.")
    content_source = payload.get("content")
    if not isinstance(content_source, dict):
        raise ValueError("Поле content должно быть объектом.")
    return content_source


def _optional_text(payload: dict[str, Any], field: str, max_chars: int) -> str | None:
    if field not in payload or not isinstance(payload[field], str):
        return None
    return payload[field].strip()[:max_chars]


def _special_action_for_file(value: str) -> str | dict[str, Any]:
    text = str(value).strip()
    if not text.startswith("{"):
        return text
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return parsed if isinstance(parsed, dict) else text
