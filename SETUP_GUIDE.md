# Siri Discord Bot: полная инструкция настройки

Этот файл можно целиком дать GPT/ChatGPT вместе с вопросом. Он описывает, как устроен бот, какие переменные нужны, как обновлять сервер, как настроить Discord и как включить leveling.

Важно: не отправляй в GPT настоящий `DISCORD_TOKEN`, пароли, IP с приватными доступами и SSH-ключи. Заменяй секреты на `***`.

## 1. Что это за бот

Репозиторий: `https://github.com/itemkey/siri_discord_bot`

Стек:

- Python 3.12
- `discord.py`
- Docker Compose
- PostgreSQL для leveling-системы

Основные файлы:

- `Dockerfile` - собирает контейнер бота.
- `docker-compose.yml` - запускает PostgreSQL и контейнер бота.
- `.env.example` - пример переменных окружения.
- `siri_bot/bot.py` - старт бота и загрузка cogs.
- `siri_bot/cogs/leveling.py` - XP, уровни, rewards, boosters и команды leveling.
- `siri_bot/leveling/` - формулы, модели и работа с PostgreSQL.

## 2. Что нужно подготовить в Discord

1. Открой Discord Developer Portal.
2. Создай Application.
3. В разделе Bot создай бота и скопируй token.
4. Включи нужные permissions при приглашении бота:

- `Send Messages`
- `Embed Links`
- `Add Reactions`
- `Read Message History`
- `Manage Messages` для `/purge`
- `Manage Roles` для role rewards и first place role

5. Пригласи бота на сервер со scopes:

- `bot`
- `applications.commands`

6. Для role rewards поставь роль бота выше всех ролей, которые он должен выдавать.

Если role reward не выдаётся, почти всегда причина такая: роль бота ниже reward-роли или у бота нет `Manage Roles`.

## 3. Как получить Discord ID

1. В Discord открой User Settings.
2. Перейди в Advanced.
3. Включи Developer Mode.
4. Правой кнопкой по серверу, пользователю, роли или каналу.
5. Нажми Copy ID.

Где нужны ID:

- `GUILD_ID` - ID сервера, чтобы slash-команды появились быстро.
- `OWNER_ID` - ID владельца/админа бота.
- `ADMIN_ROLE_IDS` - ID ролей, которым доступны админ-команды.

## 4. `.env` на сервере

На сервере должен быть файл `.env` рядом с `docker-compose.yml`.

Пример:

```env
DISCORD_TOKEN=***
GUILD_ID=123456789012345678
OWNER_ID=123456789012345678
ADMIN_ROLE_IDS=123456789012345678,234567890123456789

COMMAND_PREFIX=!
BOT_STATUS=online
LOG_LEVEL=INFO

POSTGRES_DB=siri_discord_bot
POSTGRES_USER=siri
POSTGRES_PASSWORD=change_this_password
DATABASE_URL=postgresql://siri:change_this_password@postgres:5432/siri_discord_bot
```

Правила:

- `DISCORD_TOKEN` обязателен.
- `GUILD_ID` желательно указать, иначе slash-команды могут появляться до часа.
- `OWNER_ID` и `ADMIN_ROLE_IDS` нужны для админ-команд.
- `POSTGRES_PASSWORD` и пароль внутри `DATABASE_URL` должны совпадать.
- В Docker Compose host базы данных должен быть `postgres`, не `localhost`.

## 5. Первый запуск на VPS

Команды для Ubuntu/VPS:

```bash
ssh root@SERVER_IP
git clone https://github.com/itemkey/siri_discord_bot.git
cd siri_discord_bot
cp .env.example .env
nano .env
docker compose up -d --build
docker compose logs -f discord-bot
```

Если всё хорошо, в логах будет сообщение, что бот залогинился и синхронизировал slash-команды.

## 6. Обновление уже запущенного бота

```bash
cd siri_discord_bot
git pull
docker compose up -d --build
docker compose logs -f discord-bot
```

Если после обновления добавились новые переменные в `.env.example`, перенеси их в свой `.env`.

Для leveling обязательно должны быть:

```env
POSTGRES_DB=siri_discord_bot
POSTGRES_USER=siri
POSTGRES_PASSWORD=change_this_password
DATABASE_URL=postgresql://siri:change_this_password@postgres:5432/siri_discord_bot
```

## 7. Полезные команды сервера

Статус контейнеров:

```bash
docker compose ps
```

Логи бота:

```bash
docker compose logs -f discord-bot
```

Логи PostgreSQL:

```bash
docker compose logs -f postgres
```

Перезапуск:

```bash
docker compose restart discord-bot
```

Остановка:

```bash
docker compose down
```

Остановка с удалением базы данных:

```bash
docker compose down -v
```

Не используй `docker compose down -v`, если хочешь сохранить XP и настройки leveling.

## 8. Базовые команды бота

Публичные:

- `/ping` - задержка и uptime.
- `/roll` - бросок кубиков.
- `/choose` - выбрать вариант из списка.
- `/server` - информация о сервере.
- `/avatar` - аватар пользователя.
- `/remind` - напоминание.
- `/rank` - уровень и XP пользователя.
- `/leaderboard` - таблица лидеров.

Админские:

- `/say` - отправить сообщение от имени бота.
- `/purge` - удалить сообщения.
- `/leveling ...` - настройки leveling.

## 9. Быстрая настройка leveling

1. Создай канал для level-up сообщений, например `#level-up`.
2. Настрой канал:

```text
/leveling levelup channel channel:#level-up
```

3. Настрой текст level-up сообщения:

```text
/leveling levelup message message:{user} получил(а) уровень {level}! Всего XP: {xp}
```

Доступные placeholders:

- `{user}` - mention пользователя.
- `{level}` - новый уровень.
- `{xp}` - total XP.
- `{guild}` - название сервера.

4. Проверь настройки:

```text
/leveling settings
```

5. Проверь свой ранг:

```text
/rank
```

6. Проверь leaderboard:

```text
/leaderboard
```

## 10. XP Options

По умолчанию:

- message XP: 15-25
- message cooldown: 60 секунд
- voice XP: 2 XP в минуту
- reaction XP: 2 XP автору сообщения
- reaction cooldown: 60 секунд

Пример настройки:

```text
/leveling xp-options message_min:15 message_max:25 message_cooldown:60 voice_per_minute:2 reaction_xp:2 reaction_cooldown:60
```

Чтобы временно выключить источник XP, поставь его XP в `0`:

```text
/leveling xp-options voice_per_minute:0
```

## 11. Formula

Default formula:

```text
quadratic: XP до следующего уровня = 5*L^2 + 50*L + 100
```

Где `L` - текущий уровень.

Пример оставить стандартную формулу:

```text
/leveling formula preset:quadratic a:5 b:50 c:100
```

Более простая linear formula:

```text
/leveling formula preset:linear b:100 c:100
```

## 12. Role Rewards

Role Rewards выдают роль за достижение уровня.

Добавить reward:

```text
/leveling reward add level:5 role:@Level 5
/leveling reward add level:10 role:@Level 10
```

Показать rewards:

```text
/leveling reward list
```

Удалить reward:

```text
/leveling reward remove level:5
```

Режимы:

```text
/leveling reward mode mode:accumulative
/leveling reward mode mode:highest_only
```

- `accumulative` - пользователь сохраняет все заработанные level-роли.
- `highest_only` - бот оставляет только самую высокую доступную level-роль.

## 13. First Place Role

First Place Role выдаётся пользователю на первом месте leaderboard.

Настроить:

```text
/leveling first-place-role role:@Top XP
```

Выключить:

```text
/leveling first-place-role
```

Важно: роль бота должна быть выше `@Top XP`.

## 14. XP Boosters

Поддерживаются boosters:

- `global` - для всего сервера.
- `user` - для конкретного пользователя.
- `role` - для пользователей с ролью.

Множители перемножаются:

```text
global * лучший user * лучший role
```

Итоговый cap: `5x`.

Примеры:

```text
/leveling booster add scope:global multiplier:2 duration:2h
/leveling booster add scope:user user:@User multiplier:1.5 duration:7d
/leveling booster add scope:role role:@VIP multiplier:1.25
```

Показать boosters:

```text
/leveling booster list
```

Удалить booster:

```text
/leveling booster remove booster_id:1
```

Формат duration:

- `30m` - 30 минут
- `2h` - 2 часа
- `7d` - 7 дней
- пусто - без срока

## 15. Админское управление XP

Добавить XP:

```text
/leveling member add-xp member:@User amount:500
```

Установить точное XP:

```text
/leveling member set-xp member:@User total_xp:1000
```

Сбросить XP одного пользователя:

```text
/leveling member reset member:@User
```

Сбросить XP-прогресс всего сервера:

```text
/leveling reset-confirm confirm:RESET
```

`reset-confirm` сбрасывает XP, cooldowns, reaction awards и voice sessions. Настройки, rewards и boosters остаются.

## 16. Как работает anti-farm

Message XP:

- не начисляется ботам;
- не начисляется в DM;
- начисляется только после cooldown.

Voice XP:

- не начисляется ботам;
- не начисляется в AFK-канале;
- не начисляется, если пользователь один в voice без другого не-бота;
- начисляется раз в минуту.

Reaction XP:

- начисляется автору сообщения, а не тому, кто поставил реакцию;
- не начисляется за реакции на свои сообщения;
- не начисляется ботам;
- одна и та же реакция одного пользователя на одно сообщение считается один раз;
- есть cooldown для автора сообщения.

## 17. Частые проблемы

Slash-команды не появились:

- Проверь `GUILD_ID`.
- Перезапусти бота.
- Подожди несколько минут.
- Если `GUILD_ID` пустой, глобальные команды могут появляться до часа.

Бот не запускается:

- Проверь `docker compose logs -f discord-bot`.
- Проверь, что `DISCORD_TOKEN` заполнен.
- Проверь, что PostgreSQL контейнер здоров: `docker compose ps`.

Leveling не сохраняет XP:

- Проверь `DATABASE_URL`.
- В Docker Compose host должен быть `postgres`.
- Проверь логи PostgreSQL: `docker compose logs -f postgres`.

Role Rewards не выдаются:

- У бота должно быть `Manage Roles`.
- Роль бота должна быть выше reward-роли.
- Reward-роль не должна быть managed/integration role.

Voice XP не начисляется:

- В канале должен быть минимум ещё один не-бот.
- Пользователь не должен быть в AFK-канале.
- Проверь `/leveling settings`, что `voice_xp_per_minute` больше `0`.

Reaction XP не начисляется:

- Реакцию должен поставить другой пользователь.
- Сообщение не должно быть от бота.
- Проверь cooldown reaction XP.

## 18. Что можно безопасно отправлять в GPT

Можно:

- текст ошибки из логов;
- `docker compose ps`;
- `docker compose logs --tail=100 discord-bot`;
- `.env`, но только с замазанными секретами;
- список ролей и их порядок, без приватной информации;
- какие slash-команды ты запускал и что бот ответил.

Нельзя:

- настоящий `DISCORD_TOKEN`;
- реальный пароль PostgreSQL;
- SSH private key;
- пароли от сервера;
- cookie/session tokens.

## 19. Шаблон запроса в GPT

Скопируй этот блок и вставь в GPT вместе с этим файлом:

```text
Мне нужно помочь настроить Discord-бота siri_discord_bot.

Контекст:
- Репозиторий: https://github.com/itemkey/siri_discord_bot
- Бот запускается через Docker Compose.
- В compose есть сервисы discord-bot и postgres.
- Leveling хранится в PostgreSQL.
- Секреты я замазал.

Мой .env:
DISCORD_TOKEN=***
GUILD_ID=...
OWNER_ID=...
ADMIN_ROLE_IDS=...
POSTGRES_DB=siri_discord_bot
POSTGRES_USER=siri
POSTGRES_PASSWORD=***
DATABASE_URL=postgresql://siri:***@postgres:5432/siri_discord_bot

Что я уже сделал:
1. ...
2. ...
3. ...

Команда, которая не работает:
...

Логи:
...

Помоги понять причину и дай точные команды, что сделать на сервере или в Discord.
```

## 20. Минимальный чеклист после деплоя

- [ ] В `.env` указан `DISCORD_TOKEN`.
- [ ] В `.env` указан `GUILD_ID`.
- [ ] В `.env` добавлены PostgreSQL переменные.
- [ ] `docker compose ps` показывает `discord-bot` и `postgres`.
- [ ] `/ping` отвечает.
- [ ] `/leveling settings` работает.
- [ ] Настроен `/leveling levelup channel`.
- [ ] Роль бота выше всех reward-ролей.
- [ ] `/rank` и `/leaderboard` работают.
