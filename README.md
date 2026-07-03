# Siri Discord Bot

Discord-бот на `discord.py`, готовый к запуску в Docker. Команды сделаны через slash commands, а действия разложены по отдельным cogs, чтобы их было удобно расширять.

## Что уже есть

- `/ping` - задержка и время работы бота.
- `/roll` - бросок одного или нескольких кубиков.
- `/choose` - выбор одного варианта из списка.
- `/server` - базовая информация о сервере.
- `/avatar` - аватар пользователя.
- `/remind` - напоминание в канал через заданное время.
- `/say` - отправка сообщения в канал от имени бота. Только для админов.
- `/purge` - удаление последних сообщений в текущем канале. Нужно право `Manage Messages`.

## Настройка

1. Создай приложение и бота в Discord Developer Portal.
2. При приглашении включи scopes `bot` и `applications.commands`.
3. Выдай нужные права: `Send Messages`, `Embed Links`, а для `/purge` ещё `Manage Messages`.
4. Скопируй `.env.example` в `.env` и заполни `DISCORD_TOKEN`.
5. Для быстрой регистрации команд укажи `GUILD_ID` своего Discord-сервера. Без него команды будут глобальными и могут появляться до часа.

## Запуск в Docker

```bash
docker compose up -d --build
docker compose logs -f
```

## Деплой на VPS

Подойдёт обычный VPS с Ubuntu и Docker. Домен для Discord-бота не нужен: бот сам подключается к Discord по токену.

1. Подключись к серверу:

```bash
ssh root@SERVER_IP
```

2. Установи `git`, Docker Engine и Docker Compose plugin. Актуальная инструкция Docker для Ubuntu: <https://docs.docker.com/engine/install/ubuntu/>.

3. Склонируй репозиторий:

```bash
git clone https://github.com/itemkey/siri_discord_bot.git
cd siri_discord_bot
```

4. Создай файл окружения и заполни токен:

```bash
cp .env.example .env
nano .env
```

Минимально нужен `DISCORD_TOKEN`. Для быстрой регистрации slash-команд укажи `GUILD_ID`.

5. Запусти бота:

```bash
docker compose up -d --build
docker compose logs -f discord-bot
```

Обновление после новых коммитов:

```bash
git pull
docker compose up -d --build
docker compose logs -f discord-bot
```

Остановка:

```bash
docker compose down
```

## Локальный запуск

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m siri_bot.bot
```

## Админ-доступ

Админ-команды доступны:

- пользователю с Discord ID из `OWNER_ID`;
- участникам с одной из ролей из `ADMIN_ROLE_IDS`;
- участникам с Discord-правом `Administrator`.

## Как добавлять действия

Создай новый файл в `siri_bot/cogs/`, опиши `commands.Cog` и добавь функцию `setup(bot)`. Бот автоматически загружает все cogs из этой папки при старте.

Пример:

```python
from discord import app_commands
from discord.ext import commands


class MyActions(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="hello", description="Поздороваться.")
    async def hello(self, interaction):
        await interaction.response.send_message("Hello!")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MyActions(bot))
```
