from __future__ import annotations

from io import BytesIO
from textwrap import shorten, wrap

from siri_bot.bunker.models import BunkerGame, BunkerPlayer, CARD_STAT_LABELS


def render_board_png(game: BunkerGame, players: list[BunkerPlayer]) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    width = 1200
    height = 760
    image = Image.new("RGB", (width, height), (25, 28, 35))
    draw = ImageDraw.Draw(image)
    title_font = _font(ImageFont, 36, bold=True)
    subtitle_font = _font(ImageFont, 23, bold=True)
    body_font = _font(ImageFont, 18)
    small_font = _font(ImageFont, 15)

    draw.rounded_rectangle((24, 24, width - 24, height - 24), radius=18, fill=(35, 39, 49), outline=(95, 105, 125), width=2)
    draw.text((48, 44), "Бункер", fill=(245, 246, 250), font=title_font)
    draw.text(
        (48, 90),
        f"Раунд {game.round_number}/{game.settings.rounds} | {game.state.value} | режим {game.settings.mode.value}",
        fill=(192, 202, 220),
        font=body_font,
    )

    profile = game.profile
    left_y = 135
    if profile is not None:
        left_y = _section(draw, 48, left_y, 620, "Апокалипсис", profile.apocalypse, subtitle_font, body_font)
        left_y = _section(draw, 48, left_y + 10, 620, "Схема", profile.layout, subtitle_font, body_font)
        left_y = _section(draw, 48, left_y + 10, 620, "Дефект", profile.defect, subtitle_font, body_font)
        draw.text((48, left_y + 20), "Ресурсы", fill=(245, 246, 250), font=subtitle_font)
        left_y += 58
        resources = profile.resources
        bars = (
            ("Еда", resources.food, (104, 194, 123)),
            ("Вода", resources.water, (93, 173, 226)),
            ("Электричество", resources.electricity, (245, 203, 92)),
            ("Психика", resources.morale, (187, 143, 206)),
            ("Радиация", resources.radiation, (231, 76, 60)),
        )
        for label, value, color in bars:
            _bar(draw, 48, left_y, 250, label, value, color, small_font)
            left_y += 42
    else:
        _section(draw, 48, left_y, 620, "Лобби", "Бункер еще строится. Игроки заходят и нажимают 'Готов'.", subtitle_font, body_font)

    draw.text((700, 120), "Игроки", fill=(245, 246, 250), font=subtitle_font)
    y = 165
    for index, player in enumerate(players[:14], start=1):
        status = "ведущий" if player.is_host else ("выгнан" if player.is_eliminated else ("готов" if player.ready_at else "ждет"))
        color = (92, 184, 92) if player.is_alive else (145, 145, 145)
        draw.ellipse((700, y, 734, y + 34), fill=color)
        initials = "".join(part[0] for part in player.display_name.split()[:2]).upper()[:2] or "?"
        draw.text((709, y + 7), initials, fill=(20, 22, 26), font=small_font)
        draw.text((748, y), f"{index}. {shorten(player.display_name, width=28)}", fill=(245, 246, 250), font=body_font)
        draw.text((748, y + 22), status, fill=(192, 202, 220), font=small_font)
        revealed = _revealed_line(player)
        if revealed:
            draw.text((900, y + 10), shorten(revealed, width=32), fill=(192, 202, 220), font=small_font)
        y += 45

    draw.text((48, 645), "Последние события", fill=(245, 246, 250), font=subtitle_font)
    event_y = 684
    for event in game.recent_events[-3:]:
        draw.text((48, event_y), shorten(event, width=125), fill=(205, 214, 230), font=small_font)
        event_y += 22

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _section(draw, x: int, y: int, width: int, title: str, text: str, title_font, body_font) -> int:
    draw.text((x, y), title, fill=(245, 246, 250), font=title_font)
    y += 34
    for line in wrap(text, width=62)[:4]:
        draw.text((x, y), line, fill=(205, 214, 230), font=body_font)
        y += 24
    return y


def _bar(draw, x: int, y: int, width: int, label: str, value: int, color: tuple[int, int, int], font) -> None:
    draw.text((x, y), f"{label}: {value}", fill=(230, 234, 242), font=font)
    bar_y = y + 22
    draw.rounded_rectangle((x, bar_y, x + width, bar_y + 12), radius=6, fill=(56, 61, 75))
    fill_width = int(width * max(0, min(100, value)) / 100)
    draw.rounded_rectangle((x, bar_y, x + fill_width, bar_y + 12), radius=6, fill=color)


def _revealed_line(player: BunkerPlayer) -> str:
    if not player.revealed_stats or player.card is None:
        return ""

    parts = [f"{CARD_STAT_LABELS[stat]}: {getattr(player.card, stat)}" for stat in player.revealed_stats if hasattr(player.card, stat)]
    return "; ".join(parts)


def _font(image_font_module, size: int, *, bold: bool = False):
    candidates = (
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
        "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
    )
    for candidate in candidates:
        try:
            return image_font_module.truetype(candidate, size)
        except OSError:
            continue

    return image_font_module.load_default()

