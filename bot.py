import asyncio
import logging
import os
import random
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats, BotCommandScopeChat,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import database as db
import games

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")

# Comma-separated Telegram numeric user IDs, e.g. "123456789,987654321"
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
# Single owner ID — can add/remove admins from inside the bot, no redeploy needed
OWNER_ID = int(os.getenv("OWNER_ID", "0") or "0")

DAILY_BONUS_AMOUNT = 500
DAILY_BONUS_COOLDOWN_HOURS = 24

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# active crash games: user_id -> dict(bet, multiplier, crash_point, cashed_out, task)
active_crash = {}

# active giveaway: chat_id -> dict(prize, participants:set, ends_task)
active_giveaways = {}

# active mines games: user_id -> dict(bet, mines, mine_positions, revealed, multiplier, chat_id, message_id)
active_mines = {}

# active blackjack games: user_id -> dict(bet, player, dealer, chat_id, message_id)
active_blackjack = {}

# active lottery pools: chat_id -> dict(tickets: list[(user_id, amount)], message_id)
active_lottery = {}


# ---------------- Slash-command menu (the list that pops up on "/") ----------------

BASE_COMMANDS = [
    BotCommand(command="start", description="Начать / приветствие"),
    BotCommand(command="balance", description="Баланс"),
    BotCommand(command="daily", description="Ежедневный бонус"),
    BotCommand(command="work", description="Поработать за тенге"),
    BotCommand(command="pay", description="Перевести тенге игроку"),
    BotCommand(command="mystats", description="Моя статистика"),
    BotCommand(command="top", description="Топ игроков"),
    BotCommand(command="myid", description="Мой Telegram ID"),
    BotCommand(command="channel", description="Наш канал"),
    BotCommand(command="coinflip", description="Орёл/решка"),
    BotCommand(command="dice", description="Кости"),
    BotCommand(command="slots", description="Слот-машина"),
    BotCommand(command="roulette", description="Рулетка"),
    BotCommand(command="rps", description="Камень-ножницы-бумага"),
    BotCommand(command="wheel", description="Колесо фортуны"),
    BotCommand(command="keno", description="Кено"),
    BotCommand(command="mines", description="Мины"),
    BotCommand(command="blackjack", description="Блэкджек (21)"),
    BotCommand(command="hl", description="Больше/меньше карты"),
    BotCommand(command="crash", description="Крэш-график"),
    BotCommand(command="lottery", description="Лотерея (общий банк)"),
    BotCommand(command="plinko", description="Плинко"),
    BotCommand(command="baccarat", description="Баккара"),
    BotCommand(command="roll", description="Просто бросить кубик"),
]

GROUP_ONLY_COMMANDS = [
    BotCommand(command="duel", description="Вызвать на дуэль (ответом)"),
    BotCommand(command="grouptop", description="Топ игроков этого чата"),
]

ADMIN_COMMANDS = [
    BotCommand(command="give", description="[admin] Выдать тенге"),
    BotCommand(command="take", description="[admin] Списать тенге"),
    BotCommand(command="setbalance", description="[admin] Выставить баланс"),
    BotCommand(command="resetuser", description="[admin] Сбросить баланс игрока"),
    BotCommand(command="ban", description="[admin] Заблокировать игрока"),
    BotCommand(command="unban", description="[admin] Разблокировать игрока"),
    BotCommand(command="userinfo", description="[admin] Инфо об игроке"),
    BotCommand(command="stats", description="[admin] Статистика бота"),
    BotCommand(command="groupgive", description="[admin] Раздать тенге всем в чате"),
    BotCommand(command="groupinfo", description="[admin] Инфо о группе"),
    BotCommand(command="broadcast", description="[admin] Рассылка всем игрокам"),
    BotCommand(command="giveaway", description="[admin] Запустить розыгрыш"),
    BotCommand(command="endgiveaway", description="[admin] Досрочно завершить розыгрыш"),
    BotCommand(command="economy", description="[admin] Настройки экономики"),
    BotCommand(command="admins", description="[admin] Список админов и владельца"),
]

OWNER_COMMANDS = [
    BotCommand(command="addadmin", description="[owner] Назначить админа"),
    BotCommand(command="removeadmin", description="[owner] Снять админа"),
    BotCommand(command="setminbet", description="[owner] Мин. ставка"),
    BotCommand(command="setmaxbet", description="[owner] Макс. ставка"),
    BotCommand(command="setdailybonus", description="[owner] Базовый дневной бонус"),
    BotCommand(command="setstartbalance", description="[owner] Стартовый баланс новых игроков"),
    BotCommand(command="setlotteryduration", description="[owner] Длительность раунда лотереи"),
    BotCommand(command="setlotteryedge", description="[owner] Комиссия лотереи"),
    BotCommand(command="setworkreward", description="[owner] Награда за /work"),
    BotCommand(command="setworkcooldown", description="[owner] Кулдаун /work"),
    BotCommand(command="resetalleconomy", description="[owner] Сбросить баланс всем игрокам"),
]


async def set_admin_menu(user_id: int):
    try:
        await bot.set_my_commands(BASE_COMMANDS + ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=user_id))
    except Exception:
        pass


async def set_owner_menu(user_id: int):
    try:
        await bot.set_my_commands(BASE_COMMANDS + ADMIN_COMMANDS + OWNER_COMMANDS,
                                   scope=BotCommandScopeChat(chat_id=user_id))
    except Exception:
        pass


async def reset_private_menu(user_id: int):
    try:
        await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=user_id))
    except Exception:
        pass


async def setup_default_menus():
    await bot.set_my_commands(BASE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(BASE_COMMANDS + GROUP_ONLY_COMMANDS, scope=BotCommandScopeAllGroupChats())
    if OWNER_ID:
        await set_owner_menu(OWNER_ID)
    for admin_id in db.list_admins():
        await set_admin_menu(admin_id)


def is_admin(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in ADMIN_IDS or db.is_admin_db(user_id)


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


class GroupTrackerMiddleware(BaseMiddleware):
    """Remembers which users have been active in which group chats, for /grouptop."""
    async def __call__(self, handler, event: Message, data):
        if event.chat.type != "private" and event.from_user and not event.from_user.is_bot:
            db.record_group_activity(event.chat.id, event.from_user.id)
        return await handler(event, data)


class BanMiddleware(BaseMiddleware):
    """Blocks banned users from using any command except /start."""
    async def __call__(self, handler, event: Message, data):
        if event.from_user and not event.from_user.is_bot and event.text and event.text.startswith("/"):
            command = event.text.split()[0].lstrip("/").split("@")[0]
            if command != "start" and db.is_banned(event.from_user.id):
                await event.answer("🚫 Ты заблокирован в этом боте.")
                return
        return await handler(event, data)


dp.message.middleware(GroupTrackerMiddleware())
dp.message.middleware(BanMiddleware())


@dp.errors()
async def global_error_handler(event):
    logging.exception("Unhandled error while processing update: %s", event.exception)
    return True  # mark as handled so the bot keeps polling instead of crashing


def parse_bet(arg: str, balance: int) -> int | None:
    try:
        amount = int(arg)
    except ValueError:
        return None
    min_bet = int(db.get_setting("min_bet", 10))
    max_bet = int(db.get_setting("max_bet", 50000))
    if amount < min_bet or amount > max_bet or amount > balance:
        return None
    return amount


def parse_duration(text: str) -> int | None:
    """Parses '30s', '5m', '2h', '1d' (or a plain number of seconds) into seconds."""
    text = text.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text.isdigit():
        return int(text)
    if len(text) >= 2 and text[-1] in units and text[:-1].isdigit():
        return int(text[:-1]) * units[text[-1]]
    return None


# ---------------- Basic commands ----------------

# Ссылка на канал бота — показывается кнопкой в /start и через /channel
CHANNEL_URL = "https://t.me/officail_kazic_ot_minta"


def channel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📢 Наш канал", url=CHANNEL_URL)
    ]])


@dp.message(CommandStart())
async def cmd_start(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    if message.chat.type == "private":
        if is_owner(message.from_user.id):
            await set_owner_menu(message.from_user.id)
        elif is_admin(message.from_user.id):
            await set_admin_menu(message.from_user.id)
    await message.answer(
        f"🎰 <b>Добро пожаловать в виртуальное казино!</b>\n\n"
        f"Это чисто игровой бот — тенге не настоящая, вывести её нельзя.\n"
        f"Стартовый баланс: <b>{user['balance']}</b> тенге ₸\n\n"
        f"Игр много: /coinflip, /dice, /slots, /roulette, /rps, /wheel, /keno, /mines, "
        f"/blackjack, /hl, /crash, /lottery — полный список с описанием открывается по кнопке "
        f"<b>/</b> рядом с полем ввода.\n\n"
        f"Полезное: /balance, /daily (бонус со стриком), /work (подработка), /pay (перевод тенге), "
        f"/mystats, /top.\n"
        f"В группах доступны /duel (вызов на дуэль) и /grouptop.",
        reply_markup=channel_keyboard(),
    )


@dp.message(Command("channel"))
async def cmd_channel(message: Message):
    await message.answer("📢 Наш канал:", reply_markup=channel_keyboard())


@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    if is_owner(message.from_user.id):
        role = " (владелец)"
    elif is_admin(message.from_user.id):
        role = " (админ)"
    else:
        role = ""
    await message.answer(f"₸ Твой баланс{role}: <b>{user['balance']}</b> тенге")


@dp.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")


@dp.message(Command("daily"))
async def cmd_daily(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    uid = user["user_id"]
    last_bonus_str = db.get_last_bonus(uid)
    now = datetime.now(timezone.utc)

    base_bonus = int(db.get_setting("daily_bonus", 500))
    streak_step = int(db.get_setting("daily_streak_step", 50))
    streak_cap = int(db.get_setting("daily_streak_cap", 9))
    streak = db.get_streak(uid)

    if last_bonus_str:
        last_bonus = datetime.fromisoformat(last_bonus_str)
        elapsed = now - last_bonus
        remaining = timedelta(hours=DAILY_BONUS_COOLDOWN_HOURS) - elapsed
        if remaining.total_seconds() > 0:
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            minutes = rem // 60
            await message.answer(f"⏳ Бонус уже забирал. Приходи через <b>{hours}ч {minutes}м</b>.\n"
                                  f"🔥 Серия: {streak} {'день' if streak == 1 else 'дней'}")
            return
        # missed more than 48h since last claim -> streak resets
        if elapsed > timedelta(hours=48):
            streak = 0

    streak += 1
    db.set_streak(uid, streak)
    bonus = base_bonus + streak_step * min(streak - 1, streak_cap)

    db.admin_add_balance(uid, bonus)
    db.set_last_bonus(uid, now.isoformat())
    new_balance = db.get_balance(uid)
    await message.answer(
        f"🎁 Ежедневный бонус получен: <b>+{bonus}</b> тенге!\n"
        f"🔥 Серия: <b>{streak}</b> {'день' if streak == 1 else 'дней'} подряд\n"
        f"Баланс: <b>{new_balance}</b> ₸\nПриходи через 24 часа, чтобы не сбить серию."
    )


@dp.message(Command("work"))
async def cmd_work(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    uid = user["user_id"]
    now = datetime.now(timezone.utc)
    cooldown_minutes = int(db.get_setting("work_cooldown_minutes", 30))

    last_work_str = db.get_last_work(uid)
    if last_work_str:
        elapsed = now - datetime.fromisoformat(last_work_str)
        remaining = timedelta(minutes=cooldown_minutes) - elapsed
        if remaining.total_seconds() > 0:
            minutes, seconds = divmod(int(remaining.total_seconds()), 60)
            await message.answer(f"⏳ Ты уже поработал. Отдохни ещё <b>{minutes}м {seconds}с</b>.")
            return

    work_min = int(db.get_setting("work_min", 50))
    work_max = int(db.get_setting("work_max", 300))
    reward = random.randint(work_min, work_max)
    job = games.do_work()

    db.admin_add_balance(uid, reward)
    db.set_last_work(uid, now.isoformat())
    new_balance = db.get_balance(uid)
    await message.answer(
        f"{job}\n💰 Заработал: <b>+{reward}</b> тенге\nБаланс: <b>{new_balance}</b> ₸\n"
        f"Следующая работа через {cooldown_minutes} мин."
    )


@dp.message(Command("mystats"))
async def cmd_mystats(message: Message):
    row = db.get_user_row(message.from_user.id)
    if row is None:
        row = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    await message.answer(
        f"📊 <b>Твоя статистика</b>\n\n"
        f"Баланс: <b>{row['balance']}</b> ₸\n"
        f"Сыграно игр: {row['games_played']}\n"
        f"Всего выиграно: {row['total_won']}\n"
        f"Всего проиграно: {row['total_lost']}\n"
        f"🔥 Серия ежедневных бонусов: {db.get_streak(message.from_user.id)}"
    )


@dp.message(Command("roll"))
async def cmd_roll(message: Message):
    await message.answer_dice(emoji="🎲")


@dp.message(Command("top"))
async def cmd_top(message: Message):
    staff_ids = set(db.list_admins()) | ({OWNER_ID} if OWNER_ID else set())
    rows = db.top_players_excluding(staff_ids, 10)
    if not rows:
        await message.answer("Пока нет игроков.")
        return
    text = "🏆 <b>Топ игроков:</b>\n\n"
    for i, r in enumerate(rows, 1):
        name = r["username"] or "Аноним"
        text += f"{i}. {name} — {r['balance']} ₸\n"
    await message.answer(text)


# ---------------- Coinflip ----------------

@dp.message(Command("coinflip"))
async def cmd_coinflip(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    if len(parts) != 3 or parts[2].lower() not in ("orel", "reshka"):
        await message.answer("Использование: <code>/coinflip 100 orel</code> или <code>/coinflip 100 reshka</code>")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return
    choice = parts[2].lower()
    win, label = games.play_coinflip(choice)
    if win:
        db.change_balance(user["user_id"], amount, won=True)
        await message.answer(f"₸ Выпало: <b>{label}</b>\n✅ Ты выиграл <b>{amount}</b> тенге!")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"₸ Выпало: <b>{label}</b>\n❌ Ты проиграл <b>{amount}</b> тенге.")


# ---------------- Dice ----------------

@dp.message(Command("dice"))
async def cmd_dice(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    if len(parts) != 3 or parts[2].lower() not in ("more", "less"):
        await message.answer("Использование: <code>/dice 100 more</code> (4-6) или <code>/dice 100 less</code> (1-3)")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return
    win, roll = games.play_dice(parts[2].lower())
    if win:
        db.change_balance(user["user_id"], amount, won=True)
        await message.answer(f"🎲 Выпало: <b>{roll}</b>\n✅ Ты выиграл <b>{amount}</b> тенге!")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎲 Выпало: <b>{roll}</b>\n❌ Ты проиграл <b>{amount}</b> тенге.")


# ---------------- Slots ----------------

@dp.message(Command("slots"))
async def cmd_slots(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/slots 100</code>")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return
    reels, multiplier = games.spin_slots()
    reels_text = " | ".join(reels)
    if multiplier > 0:
        winnings = int(amount * multiplier)
        db.change_balance(user["user_id"], winnings - amount, won=True)
        await message.answer(f"🎰 [ {reels_text} ]\n✅ Множитель x{multiplier}! Выигрыш: <b>{winnings}</b> тенге")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎰 [ {reels_text} ]\n❌ Проигрыш <b>{amount}</b> тенге.")


# ---------------- Roulette ----------------

@dp.message(Command("roulette"))
async def cmd_roulette(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/roulette 100 red</code>\n"
                              "Варианты ставки: red/black/green, even/odd, low/high (1-18/19-36), "
                              "или число 0-36")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return
    bet = parts[2].lower()
    if bet not in ("red", "black", "green", "even", "odd", "low", "high"):
        if not (bet.isdigit() and 0 <= int(bet) <= 36):
            await message.answer("Ставка должна быть red/black/green, even/odd, low/high или числом от 0 до 36.")
            return
    number, color = games.spin_roulette()
    win, multiplier = games.check_roulette_win(bet, number, color)
    color_ru = {"red": "🔴 Красное", "black": "⚫ Чёрное", "green": "🟢 Зеро"}[color]
    if win:
        winnings = int(amount * multiplier)
        db.change_balance(user["user_id"], winnings - amount, won=True)
        await message.answer(f"🎡 Выпало: <b>{number}</b> ({color_ru})\n"
                              f"✅ Выигрыш x{multiplier}: <b>{winnings}</b> тенге!")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎡 Выпало: <b>{number}</b> ({color_ru})\n"
                              f"❌ Проигрыш <b>{amount}</b> тенге.")


# ---------------- Rock-Paper-Scissors ----------------

RPS_RU = {"rock": "Камень 🪨", "paper": "Бумага 📄", "scissors": "Ножницы ✂️"}
RPS_ALIASES = {"камень": "rock", "бумага": "paper", "ножницы": "scissors",
               "rock": "rock", "paper": "paper", "scissors": "scissors"}


@dp.message(Command("rps"))
async def cmd_rps(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    if len(parts) != 3 or parts[2].lower() not in RPS_ALIASES:
        await message.answer("Использование: <code>/rps 100 rock</code> (rock/paper/scissors "
                              "или камень/бумага/ножницы)")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return
    choice = RPS_ALIASES[parts[2].lower()]
    bot_choice, result = games.play_rps(choice)
    text = f"Ты: {RPS_RU[choice]}\nБот: {RPS_RU[bot_choice]}\n\n"
    if result == "win":
        db.change_balance(user["user_id"], amount, won=True)
        await message.answer(text + f"✅ Победа! Выигрыш: <b>{amount}</b> тенге")
    elif result == "lose":
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(text + f"❌ Проигрыш <b>{amount}</b> тенге.")
    else:
        await message.answer(text + "🤝 Ничья, ставка возвращена.")


# ---------------- Wheel of fortune ----------------

@dp.message(Command("wheel"))
async def cmd_wheel(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/wheel 100</code>")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return
    multiplier = games.spin_wheel()
    if multiplier > 0:
        winnings = int(amount * multiplier)
        db.change_balance(user["user_id"], winnings - amount, won=(multiplier > 1))
        await message.answer(f"🎡 Колесо остановилось на <b>x{multiplier}</b>!\nВыигрыш: <b>{winnings}</b> тенге")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎡 Колесо остановилось на <b>x0</b>...\n❌ Проигрыш <b>{amount}</b> тенге.")


# ---------------- Keno ----------------

@dp.message(Command("keno"))
async def cmd_keno(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/keno 100 3,7,15</code> — ставка и от 1 до 4 чисел "
                              "от 1 до 20 через запятую. Бот вытягивает 8 чисел, оплата по числу совпадений.")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return
    try:
        picks = [int(x) for x in parts[2].split(",")]
    except ValueError:
        await message.answer("Числа должны быть целыми, через запятую без пробелов, например 3,7,15.")
        return
    if not (1 <= len(picks) <= 4) or len(set(picks)) != len(picks) or any(not (1 <= p <= 20) for p in picks):
        await message.answer("Выбери от 1 до 4 разных чисел в диапазоне 1-20.")
        return

    drawn, matches, multiplier = games.play_keno(picks)
    drawn_str = ", ".join(str(x) for x in sorted(drawn))
    if multiplier > 0:
        winnings = int(amount * multiplier)
        db.change_balance(user["user_id"], winnings - amount, won=True)
        await message.answer(f"🎱 Выпало: {drawn_str}\nСовпадений: {matches}/{len(picks)}\n"
                              f"✅ Выигрыш x{multiplier}: <b>{winnings}</b> тенге")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎱 Выпало: {drawn_str}\nСовпадений: {matches}/{len(picks)}\n"
                              f"❌ Проигрыш <b>{amount}</b> тенге.")


# ---------------- Lottery pool (общий банк, шанс пропорционален ставке) ----------------

async def finish_lottery(chat_id: int):
    state = active_lottery.pop(chat_id, None)
    if state is None or not state["tickets"]:
        if state is not None:
            await bot.send_message(chat_id, "🎟 Лотерея завершена — никто не купил билет 😢")
        return
    tickets = state["tickets"]
    pot = sum(amount for _, amount in tickets)
    house_edge = float(db.get_setting("lottery_house_edge", 0.05))
    payout = int(pot * (1 - house_edge))
    winner_id = random.choices([uid for uid, _ in tickets], weights=[amt for _, amt in tickets], k=1)[0]
    db.admin_add_balance(winner_id, payout)
    try:
        winner_chat = await bot.get_chat(winner_id)
        winner_name = f"@{winner_chat.username}" if winner_chat.username else winner_chat.first_name
    except Exception:
        winner_name = str(winner_id)
    participants = len(set(uid for uid, _ in tickets))
    await bot.send_message(
        chat_id,
        f"🎟 <b>Лотерея завершена!</b>\nУчастников: {participants}\nБанк: {pot} ₸\n"
        f"🏆 Победитель: {winner_name}\nВыигрыш: <b>{payout}</b> тенге ₸"
    )


@dp.message(Command("lottery"))
async def cmd_lottery(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/lottery 100</code> — купить билет. Шанс выиграть весь "
                              "банк пропорционален сумме твоего билета относительно суммы всех билетов.")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return

    chat_id = message.chat.id
    db.change_balance(user["user_id"], -amount)

    is_new_round = chat_id not in active_lottery
    if is_new_round:
        active_lottery[chat_id] = {"tickets": []}
    active_lottery[chat_id]["tickets"].append((user["user_id"], amount))

    pot = sum(a for _, a in active_lottery[chat_id]["tickets"])
    duration = int(db.get_setting("lottery_duration_seconds", 120))

    if is_new_round:
        await message.answer(
            f"🎟 <b>Новый розыгрыш лотереи начат!</b>\nТвой билет: {amount}\nБанк: {pot} ₸\n"
            f"Покупай билет командой <code>/lottery [сумма]</code>. Итоги через {duration} сек."
        )
        asyncio.create_task(_lottery_timer(chat_id, duration))
    else:
        await message.answer(f"🎟 Билет куплен: {amount}. Текущий банк: <b>{pot}</b> ₸")


async def _lottery_timer(chat_id: int, duration: int):
    await asyncio.sleep(duration)
    await finish_lottery(chat_id)


# ---------------- Plinko ----------------

@dp.message(Command("plinko"))
async def cmd_plinko(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/plinko 100</code> — шарик падает вниз по доске, "
                              "множитель зависит от того, в какую лунку он попадёт (края — редко, но x8).")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return
    slot, multiplier = games.drop_plinko_ball()
    board = " ".join("🔴" if i == slot else "⚪" for i in range(len(games.PLINKO_MULTIPLIERS)))
    if multiplier > 0:
        winnings = int(amount * multiplier)
        db.change_balance(user["user_id"], winnings - amount, won=(multiplier >= 1))
        await message.answer(f"🎯 {board}\nШарик попал в лунку x{multiplier}!\n"
                              f"{'✅ Выигрыш' if multiplier >= 1 else '➖ Частичный возврат'}: <b>{winnings}</b> тенге")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎯 {board}\n❌ Проигрыш <b>{amount}</b> тенге.")


# ---------------- Baccarat ----------------

@dp.message(Command("baccarat"))
async def cmd_baccarat(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    aliases = {"player": "player", "игрок": "player", "banker": "banker", "банкир": "banker",
               "tie": "tie", "ничья": "tie"}
    if len(parts) != 3 or parts[2].lower() not in aliases:
        await message.answer("Использование: <code>/baccarat 100 player</code> (player/banker/tie) — "
                              "ставка на игрока x2, на банкира x1.95, на ничью x8")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return
    bet = aliases[parts[2].lower()]
    player_val, banker_val, outcome = games.play_baccarat(bet)
    text = f"🎴 Игрок: <b>{player_val}</b> | Банкир: <b>{banker_val}</b>\n\n"
    if outcome == bet:
        multiplier = {"player": 2.0, "banker": 1.95, "tie": 8.0}[bet]
        winnings = int(amount * multiplier)
        db.change_balance(user["user_id"], winnings - amount, won=True)
        await message.answer(text + f"✅ Победил {outcome}! Выигрыш x{multiplier}: <b>{winnings}</b> тенге")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(text + f"❌ Победил {outcome}. Проигрыш <b>{amount}</b> тенге.")


# ---------------- Mines ----------------

def mines_keyboard(uid: int) -> InlineKeyboardMarkup:
    state = active_mines[uid]
    rows = []
    for r in range(games.GRID_SIZE):
        row = []
        for c in range(games.GRID_SIZE):
            if (r, c) in state["revealed"]:
                row.append(InlineKeyboardButton(text="💎", callback_data="noop"))
            else:
                row.append(InlineKeyboardButton(text="⬜", callback_data=f"mine:{uid}:{r}:{c}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text=f"💰 Забрать (x{state['multiplier']})",
                                       callback_data=f"minecash:{uid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(Command("mines"))
async def cmd_mines(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    uid = user["user_id"]
    if uid in active_mines:
        await message.answer("У тебя уже есть активная игра в /mines!")
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/mines 100 3</code> (ставка, кол-во мин от 1 до 24)")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return
    try:
        mines_count = int(parts[2])
    except ValueError:
        mines_count = -1
    if not (1 <= mines_count <= 24):
        await message.answer("Количество мин должно быть от 1 до 24.")
        return

    db.change_balance(uid, -amount)
    active_mines[uid] = {
        "bet": amount,
        "mines_count": mines_count,
        "mine_positions": games.generate_mine_positions(mines_count),
        "revealed": set(),
        "multiplier": 1.0,
    }
    sent = await message.answer(
        f"💣 <b>Мины!</b>\nСтавка: {amount} | Мин на поле: {mines_count}\nМножитель: <b>x1.0</b>\n"
        f"Открывай клетки — чем больше открыл, тем выше множитель. Забери выигрыш вовремя!",
        reply_markup=mines_keyboard(uid),
    )
    active_mines[uid]["chat_id"] = sent.chat.id
    active_mines[uid]["message_id"] = sent.message_id


@dp.callback_query(F.data.startswith("mine:"))
async def cb_mine_click(callback: CallbackQuery):
    _, uid_str, r_str, c_str = callback.data.split(":")
    uid = int(uid_str)
    if callback.from_user.id != uid:
        await callback.answer("Это не твоя игра!", show_alert=True)
        return
    state = active_mines.get(uid)
    if not state:
        await callback.answer("Игра уже завершена.", show_alert=True)
        return
    cell = (int(r_str), int(c_str))
    if cell in state["revealed"]:
        await callback.answer()
        return

    if cell in state["mine_positions"]:
        # boom — reveal everything, lose the bet
        rows = []
        for r in range(games.GRID_SIZE):
            row = []
            for c in range(games.GRID_SIZE):
                if (r, c) in state["mine_positions"]:
                    row.append(InlineKeyboardButton(text="💣", callback_data="noop"))
                elif (r, c) in state["revealed"]:
                    row.append(InlineKeyboardButton(text="💎", callback_data="noop"))
                else:
                    row.append(InlineKeyboardButton(text="⬜", callback_data="noop"))
            rows.append(row)
        active_mines.pop(uid, None)
        await bot.edit_message_text(
            chat_id=state["chat_id"], message_id=state["message_id"],
            text=f"💥 <b>БУМ!</b>\nСтавка {state['bet']} сгорела.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        await callback.answer("Подорвался! 💣")
        return

    state["revealed"].add(cell)
    state["multiplier"] = games.next_mines_multiplier(
        state["multiplier"], len(state["revealed"]), state["mines_count"]
    )
    total_cells = games.GRID_SIZE * games.GRID_SIZE
    if len(state["revealed"]) == total_cells - state["mines_count"]:
        # opened every safe cell — auto cash out
        winnings = int(state["bet"] * state["multiplier"])
        db.change_balance(uid, winnings, won=True)
        active_mines.pop(uid, None)
        await bot.edit_message_text(
            chat_id=state["chat_id"], message_id=state["message_id"],
            text=f"🏆 <b>Все безопасные клетки открыты!</b>\nВыигрыш x{state['multiplier']}: <b>{winnings}</b> тенге ₸",
        )
        await callback.answer("Идеальная игра!")
        return

    await bot.edit_message_text(
        chat_id=state["chat_id"], message_id=state["message_id"],
        text=(f"💣 <b>Мины!</b>\nСтавка: {state['bet']} | Мин на поле: {state['mines_count']}\n"
              f"Множитель: <b>x{state['multiplier']}</b>\nОткрыто: {len(state['revealed'])}"),
        reply_markup=mines_keyboard(uid),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("minecash:"))
async def cb_mine_cashout(callback: CallbackQuery):
    uid = int(callback.data.split(":")[1])
    if callback.from_user.id != uid:
        await callback.answer("Это не твоя игра!", show_alert=True)
        return
    state = active_mines.get(uid)
    if not state:
        await callback.answer("Игра уже завершена.", show_alert=True)
        return
    if not state["revealed"]:
        await callback.answer("Открой хотя бы одну клетку перед тем, как забирать!", show_alert=True)
        return
    winnings = int(state["bet"] * state["multiplier"])
    db.change_balance(uid, winnings, won=True)
    active_mines.pop(uid, None)
    await bot.edit_message_text(
        chat_id=state["chat_id"], message_id=state["message_id"],
        text=f"✅ <b>Забрал на x{state['multiplier']}!</b>\nВыигрыш: <b>{winnings}</b> тенге ₸",
    )
    await callback.answer("Забрал выигрыш!")


@dp.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


# ---------------- Higher / Lower ----------------

@dp.message(Command("higherlower", "hl"))
async def cmd_higherlower(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    aliases = {"higher": "higher", "больше": "higher", "lower": "lower", "меньше": "lower"}
    if len(parts) != 3 or parts[2].lower() not in aliases:
        await message.answer("Использование: <code>/hl 100 higher</code> (higher/lower или больше/меньше) — "
                              "угадай, будет вторая карта больше или меньше первой")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return
    guess = aliases[parts[2].lower()]
    first, second, outcome = games.play_higher_lower(guess)
    text = f"Первая карта: <b>{first[0]}{first[1]}</b>\nВторая карта: <b>{second[0]}{second[1]}</b>\n\n"
    if outcome == "win":
        db.change_balance(user["user_id"], amount, won=True)
        await message.answer(text + f"✅ Угадал! Выигрыш: <b>{amount}</b> тенге")
    elif outcome == "lose":
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(text + f"❌ Не угадал. Проигрыш <b>{amount}</b> тенге.")
    else:
        await message.answer(text + "🤝 Карты равны — ставка возвращена.")


# ---------------- Blackjack (21) ----------------

def blackjack_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🃏 Ещё карту", callback_data="bj_hit"),
        InlineKeyboardButton(text="✋ Хватит", callback_data="bj_stand"),
    ]])


def blackjack_text(state: dict, reveal_dealer: bool = False) -> str:
    player_val = games.hand_value(state["player"])
    if reveal_dealer:
        dealer_str = f"{games.format_hand(state['dealer'])} (={games.hand_value(state['dealer'])})"
    else:
        hidden = f"{state['dealer'][0][0]}{state['dealer'][0][1]} 🂠"
        dealer_str = hidden
    return (f"🃏 <b>Блэкджек</b>\nСтавка: {state['bet']}\n\n"
            f"Дилер: {dealer_str}\n"
            f"Твои карты: {games.format_hand(state['player'])} (={player_val})")


async def resolve_blackjack(uid: int, chat_id: int, message_id: int):
    state = active_blackjack.pop(uid, None)
    if state is None:
        return
    player_val = games.hand_value(state["player"])
    if player_val > 21:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                     text=blackjack_text(state, reveal_dealer=True) + "\n\n💥 Перебор! Проигрыш.")
        return

    games.dealer_play(state["dealer"])
    dealer_val = games.hand_value(state["dealer"])
    bet = state["bet"]

    if games.is_natural_blackjack(state["player"]) and not games.is_natural_blackjack(state["dealer"]):
        winnings = int(bet * 2.5)
        db.change_balance(uid, winnings, won=True)
        result = f"🏆 Блэкджек! Выигрыш x2.5: <b>{winnings}</b> тенге"
    elif dealer_val > 21 or player_val > dealer_val:
        winnings = bet * 2
        db.change_balance(uid, winnings, won=True)
        result = f"✅ Победа! Выигрыш: <b>{winnings}</b> тенге"
    elif player_val == dealer_val:
        db.change_balance(uid, bet)  # push, return the bet
        result = "🤝 Ничья, ставка возвращена."
    else:
        result = f"❌ Дилер сильнее. Проигрыш <b>{bet}</b> тенге."

    await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                 text=blackjack_text(state, reveal_dealer=True) + f"\n\n{result}")


@dp.message(Command("blackjack", "bj"))
async def cmd_blackjack(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    uid = user["user_id"]
    if uid in active_blackjack:
        await message.answer("У тебя уже есть активная игра в /blackjack!")
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/blackjack 100</code>")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return

    db.change_balance(uid, -amount)
    state = {
        "bet": amount,
        "player": [games.draw_card(), games.draw_card()],
        "dealer": [games.draw_card(), games.draw_card()],
    }
    active_blackjack[uid] = state

    if games.is_natural_blackjack(state["player"]):
        sent = await message.answer(blackjack_text(state, reveal_dealer=True))
        await resolve_blackjack_natural(uid, sent.chat.id, sent.message_id, state)
        return

    sent = await message.answer(blackjack_text(state), reply_markup=blackjack_keyboard())
    state["chat_id"] = sent.chat.id
    state["message_id"] = sent.message_id


async def resolve_blackjack_natural(uid: int, chat_id: int, message_id: int, state: dict):
    # re-store so resolve_blackjack can pop it cleanly and reuse the same payout logic
    active_blackjack[uid] = state
    await resolve_blackjack(uid, chat_id, message_id)


@dp.callback_query(F.data == "bj_hit")
async def cb_bj_hit(callback: CallbackQuery):
    uid = callback.from_user.id
    state = active_blackjack.get(uid)
    if not state:
        await callback.answer("Игра уже завершена.", show_alert=True)
        return
    state["player"].append(games.draw_card())
    if games.hand_value(state["player"]) >= 21:
        await callback.answer()
        await resolve_blackjack(uid, state["chat_id"], state["message_id"])
        return
    await bot.edit_message_text(chat_id=state["chat_id"], message_id=state["message_id"],
                                 text=blackjack_text(state), reply_markup=blackjack_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "bj_stand")
async def cb_bj_stand(callback: CallbackQuery):
    uid = callback.from_user.id
    state = active_blackjack.get(uid)
    if not state:
        await callback.answer("Игра уже завершена.", show_alert=True)
        return
    await callback.answer()
    await resolve_blackjack(uid, state["chat_id"], state["message_id"])


# ---------------- Crash (имитация трейдинга) ----------------

def crash_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Забрать", callback_data=f"cashout:{user_id}")]
    ])


@dp.message(Command("crash"))
async def cmd_crash(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    uid = user["user_id"]
    if uid in active_crash:
        await message.answer("У тебя уже есть активная игра в /crash!")
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/crash 100</code>")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return

    db.change_balance(uid, -amount)  # hold the bet upfront
    crash_point = games.generate_crash_point()

    sent = await message.answer(
        f"📈 <b>Крэш-график запущен!</b>\nСтавка: {amount}\nМножитель: <b>x1.00</b>\n\nЖми «Забрать», пока не рухнуло!",
        reply_markup=crash_keyboard(uid),
    )

    state = {
        "bet": amount,
        "multiplier": 1.0,
        "crash_point": crash_point,
        "cashed_out": False,
        "chat_id": sent.chat.id,
        "message_id": sent.message_id,
    }
    active_crash[uid] = state

    async def run_game():
        try:
            while True:
                await asyncio.sleep(0.8)
                state["multiplier"] = round(state["multiplier"] + random.uniform(0.05, 0.25) * state["multiplier"], 2)
                if state["cashed_out"]:
                    return
                if state["multiplier"] >= state["crash_point"]:
                    await bot.edit_message_text(
                        chat_id=state["chat_id"],
                        message_id=state["message_id"],
                        text=(f"📉 <b>КРАХ на x{state['crash_point']}!</b>\n"
                              f"Ставка {state['bet']} сгорела. Не успел забрать 😢"),
                    )
                    active_crash.pop(uid, None)
                    return
                await bot.edit_message_text(
                    chat_id=state["chat_id"],
                    message_id=state["message_id"],
                    text=(f"📈 <b>Крэш-график летит!</b>\nСтавка: {state['bet']}\n"
                          f"Множитель: <b>x{state['multiplier']}</b>\n\nЖми «Забрать», пока не рухнуло!"),
                    reply_markup=crash_keyboard(uid),
                )
        except Exception:
            active_crash.pop(uid, None)

    asyncio.create_task(run_game())


@dp.callback_query(F.data.startswith("cashout:"))
async def cb_cashout(callback: CallbackQuery):
    target_uid = int(callback.data.split(":")[1])
    if callback.from_user.id != target_uid:
        await callback.answer("Это не твоя игра!", show_alert=True)
        return
    state = active_crash.get(target_uid)
    if not state or state["cashed_out"]:
        await callback.answer("Игра уже завершена.", show_alert=True)
        return
    state["cashed_out"] = True
    winnings = int(state["bet"] * state["multiplier"])
    db.change_balance(target_uid, winnings, won=True)
    active_crash.pop(target_uid, None)
    await bot.edit_message_text(
        chat_id=state["chat_id"],
        message_id=state["message_id"],
        text=(f"✅ <b>Забрал на x{state['multiplier']}!</b>\n"
              f"Выигрыш: <b>{winnings}</b> тенге ₸"),
    )
    await callback.answer("Успел забрать!")


# ---------------- Admin: give currency ----------------

@dp.message(Command("give"))
async def cmd_give(message: Message):
    if not is_admin(message.from_user.id):
        return  # silently ignore for non-admins
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/give [user_id] [сумма]</code>\n"
                              "Сумма может быть отрицательной, чтобы забрать тенге.")
        return
    try:
        target_id = int(parts[1])
        amount = int(parts[2])
    except ValueError:
        await message.answer("user_id и сумма должны быть числами.")
        return
    if not db.user_exists(target_id):
        await message.answer("Такого пользователя нет в базе (он ещё не запускал /start у бота).")
        return
    db.admin_add_balance(target_id, amount)
    new_balance = db.get_balance(target_id)
    await message.answer(f"✅ Игроку <code>{target_id}</code> начислено {amount}. Новый баланс: {new_balance}")
    try:
        sign = "➕" if amount >= 0 else "➖"
        await bot.send_message(target_id, f"{sign} Администратор изменил твой баланс на {amount}.\n"
                                           f"Текущий баланс: <b>{new_balance}</b> ₸")
    except Exception:
        pass  # user may have blocked the bot


@dp.message(Command("take"))
async def cmd_take(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/take [user_id] [сумма]</code> — списать тенге у игрока "
                              "(сумма всегда положительная; если у игрока меньше — спишется весь баланс).")
        return
    try:
        target_id = int(parts[1])
        amount = int(parts[2])
    except ValueError:
        await message.answer("user_id и сумма должны быть числами.")
        return
    if amount <= 0:
        await message.answer("Сумма списания должна быть положительной.")
        return
    if not db.user_exists(target_id):
        await message.answer("Такого пользователя нет в базе.")
        return
    removed = db.take_balance(target_id, amount)
    new_balance = db.get_balance(target_id)
    await message.answer(f"✅ У игрока <code>{target_id}</code> списано {removed} тенге. Баланс: {new_balance}")
    try:
        await bot.send_message(target_id, f"➖ Администратор списал <b>{removed}</b> тенге.\n"
                                           f"Текущий баланс: <b>{new_balance}</b> ₸")
    except Exception:
        pass


@dp.message(Command("resetuser"))
async def cmd_resetuser(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/resetuser [user_id]</code> — сбросить баланс игрока "
                              "до стартового значения.")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("user_id должен быть числом.")
        return
    if not db.user_exists(target_id):
        await message.answer("Такого пользователя нет в базе.")
        return
    start = int(db.get_setting("start_balance", db.START_BALANCE))
    db.set_balance(target_id, start)
    await message.answer(f"✅ Баланс игрока <code>{target_id}</code> сброшен до {start} тенге.")


@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    target_id = None
    if len(parts) == 2:
        try:
            target_id = int(parts[1])
        except ValueError:
            await message.answer("user_id должен быть числом.")
            return
    elif message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        await message.answer("Использование: <code>/ban [user_id]</code> или ответом на сообщение игрока.")
        return
    if is_admin(target_id):
        await message.answer("Нельзя забанить админа или владельца.")
        return
    db.get_or_create_user(target_id, str(target_id))
    db.set_banned(target_id, True)
    await message.answer(f"🚫 Игрок <code>{target_id}</code> заблокирован.")


@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/unban [user_id]</code>")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("user_id должен быть числом.")
        return
    db.set_banned(target_id, False)
    await message.answer(f"✅ Игрок <code>{target_id}</code> разблокирован.")


# ---------------- Admin: setbalance, userinfo, bot stats ----------------

@dp.message(Command("setbalance"))
async def cmd_setbalance(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/setbalance [user_id] [сумма]</code> — жёстко "
                              "выставить баланс игрока (в отличие от /give, который добавляет/вычитает).")
        return
    try:
        target_id = int(parts[1])
        amount = int(parts[2])
    except ValueError:
        await message.answer("user_id и сумма должны быть числами.")
        return
    if amount < 0:
        await message.answer("Баланс не может быть отрицательным.")
        return
    if not db.user_exists(target_id):
        await message.answer("Такого пользователя нет в базе.")
        return
    db.set_balance(target_id, amount)
    await message.answer(f"✅ Баланс игрока <code>{target_id}</code> выставлен в {amount}.")


@dp.message(Command("userinfo"))
async def cmd_userinfo(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    target_id = None
    if len(parts) == 2:
        try:
            target_id = int(parts[1])
        except ValueError:
            await message.answer("user_id должен быть числом.")
            return
    elif message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        await message.answer("Использование: <code>/userinfo [user_id]</code> или ответом на сообщение игрока.")
        return

    row = db.get_user_row(target_id)
    if row is None:
        await message.answer("Такого пользователя нет в базе.")
        return
    last_bonus = row["last_bonus"] or "никогда"
    await message.answer(
        f"👤 <b>Игрок {target_id}</b>\n"
        f"Ник: {row['username'] or '—'}\n"
        f"Баланс: {row['balance']} ₸\n"
        f"Сыграно игр: {row['games_played']}\n"
        f"Выиграно всего: {row['total_won']}\n"
        f"Проиграно всего: {row['total_lost']}\n"
        f"Серия бонусов: {row['streak_days']}\n"
        f"Последний бонус: {last_bonus}\n"
        f"Админ: {'да' if db.is_admin_db(target_id) or target_id == OWNER_ID else 'нет'}"
    )


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    s = db.get_bot_stats()
    await message.answer(
        f"📈 <b>Статистика бота</b>\n\n"
        f"Всего игроков: {s['total_users']}\n"
        f"Суммарный баланс в экономике: {s['total_balance']} ₸\n"
        f"Всего сыграно игр: {s['total_games']}"
    )


@dp.message(Command("groupgive"))
async def cmd_groupgive(message: Message):
    if not is_admin(message.from_user.id):
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Эта команда работает только в групповых чатах.")
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/groupgive [сумма]</code> — начислить всем известным "
                              "игрокам этого чата.")
        return
    try:
        amount = int(parts[1])
    except ValueError:
        await message.answer("Сумма должна быть числом.")
        return
    user_ids = db.get_group_user_ids(message.chat.id)
    if not user_ids:
        await message.answer("В этом чате пока нет отслеженных игроков.")
        return
    for uid in user_ids:
        db.admin_add_balance(uid, amount)
    await message.answer(f"✅ Начислено {amount} тенге {len(user_ids)} игрокам этого чата.")


@dp.message(Command("groupinfo"))
async def cmd_groupinfo(message: Message):
    if not is_admin(message.from_user.id):
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Эта команда работает только в групповых чатах.")
        return
    user_ids = db.get_group_user_ids(message.chat.id)
    total_balance = sum(db.get_balance(uid) for uid in user_ids)
    await message.answer(
        f"ℹ️ <b>Инфо о чате</b>\n\n"
        f"Chat ID: <code>{message.chat.id}</code>\n"
        f"Отслеженных игроков: {len(user_ids)}\n"
        f"Их суммарный баланс: {total_balance} ₸"
    )


# ---------------- Admin: broadcast (реклама/объявления) ----------------

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if not is_admin(message.from_user.id):
        return
    text = message.text.partition(" ")[2].strip()
    if not text:
        await message.answer("Использование: <code>/broadcast Текст сообщения для всех игроков</code>")
        return
    user_ids = db.get_all_user_ids()
    sent, failed = 0, 0
    status = await message.answer(f"Рассылка запущена на {len(user_ids)} игроков...")
    for uid in user_ids:
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # avoid hitting Telegram rate limits
    await status.edit_text(f"✅ Рассылка завершена.\nДоставлено: {sent}\nНе доставлено: {failed}")


# ---------------- Admin: giveaway (розыгрыш) ----------------

def giveaway_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎉 Участвовать", callback_data="giveaway_join")]
    ])


def format_duration(seconds: int) -> str:
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"{seconds // 86400} д."
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600} ч."
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60} мин."
    return f"{seconds} сек."


@dp.message(Command("giveaway"))
async def cmd_giveaway(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 4:
        await message.answer(
            "Использование: <code>/giveaway [приз] [макс_участников] [время]</code>\n"
            "Макс. участников: число, или <code>0</code> — без ограничения.\n"
            "Время: секунды или с суффиксом s/m/h/d, например <code>30m</code>, <code>2h</code>, <code>1d</code>.\n"
            "Пример: <code>/giveaway 1000 0 2h</code>"
        )
        return
    try:
        prize = int(parts[1])
        max_participants = int(parts[2])
    except ValueError:
        await message.answer("Приз и лимит участников должны быть числами.")
        return
    duration = parse_duration(parts[3])
    if duration is None or duration <= 0:
        await message.answer("Не понял время. Примеры: 30s, 10m, 2h, 1d, либо просто число секунд.")
        return
    if max_participants < 0:
        await message.answer("Лимит участников не может быть отрицательным.")
        return

    chat_id = message.chat.id
    if chat_id in active_giveaways:
        await message.answer("В этом чате уже идёт розыгрыш!")
        return

    cap_text = "без ограничения" if max_participants == 0 else str(max_participants)
    sent = await message.answer(
        f"🎉 <b>РОЗЫГРЫШ!</b>\nПриз: <b>{prize}</b> тенге ₸\nМест: {cap_text}\n"
        f"Жми кнопку, чтобы участвовать!\nИтоги через {format_duration(duration)}.",
        reply_markup=giveaway_keyboard(),
    )
    active_giveaways[chat_id] = {
        "prize": prize,
        "participants": set(),
        "max_participants": max_participants,
        "message_id": sent.message_id,
    }

    asyncio.create_task(_giveaway_timer(chat_id, duration))


async def _giveaway_timer(chat_id: int, duration: int):
    await asyncio.sleep(duration)
    await finish_giveaway(chat_id)


async def finish_giveaway(chat_id: int):
    state = active_giveaways.pop(chat_id, None)
    if state is None:
        return
    participants = list(state["participants"])
    if not participants:
        await bot.send_message(chat_id, "🎉 Розыгрыш завершён — никто не участвовал 😢")
        return
    winner_id = random.choice(participants)
    db.admin_add_balance(winner_id, state["prize"])
    try:
        winner_chat = await bot.get_chat(winner_id)
        winner_name = f"@{winner_chat.username}" if winner_chat.username else winner_chat.first_name
    except Exception:
        winner_name = str(winner_id)
    await bot.send_message(
        chat_id,
        f"🎉 <b>Розыгрыш завершён!</b>\nУчастников: {len(participants)}\n"
        f"Победитель: {winner_name}\nВыигрыш: <b>{state['prize']}</b> тенге ₸"
    )


@dp.callback_query(F.data == "giveaway_join")
async def cb_giveaway_join(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    state = active_giveaways.get(chat_id)
    if state is None:
        await callback.answer("Розыгрыш уже завершён.", show_alert=True)
        return
    if callback.from_user.id in state["participants"]:
        await callback.answer("Ты уже участвуешь!")
        return
    cap = state["max_participants"]
    if cap and len(state["participants"]) >= cap:
        await callback.answer("Мест больше нет 😢", show_alert=True)
        return
    db.get_or_create_user(callback.from_user.id, callback.from_user.username or callback.from_user.first_name)
    state["participants"].add(callback.from_user.id)
    if cap and len(state["participants"]) >= cap:
        # room filled — end the giveaway immediately instead of waiting for the timer
        await callback.answer("Ты в деле! Мест больше нет — розыгрыш завершается 🎉")
        await finish_giveaway(chat_id)
        return
    await callback.answer("Участвуешь в розыгрыше! 🎉")


@dp.message(Command("endgiveaway"))
async def cmd_endgiveaway(message: Message):
    if not is_admin(message.from_user.id):
        return
    chat_id = message.chat.id
    if chat_id not in active_giveaways:
        await message.answer("В этом чате сейчас нет активного розыгрыша.")
        return
    await finish_giveaway(chat_id)


# ---------------- Transfer currency between players ----------------

MIN_TRANSFER = 1


@dp.message(Command("pay", "transfer"))
async def cmd_pay(message: Message):
    sender = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)

    target_id = None
    amount_str = None

    if message.reply_to_message and message.reply_to_message.from_user:
        # /pay 100  (as a reply to the recipient's message)
        target_user_obj = message.reply_to_message.from_user
        parts = message.text.split()
        if len(parts) != 2:
            await message.answer("Использование: <code>/pay 100</code> (ответом на сообщение получателя)\n"
                                  "или <code>/pay [user_id] 100</code>")
            return
        if target_user_obj.is_bot:
            await message.answer("Нельзя перевести тенге боту.")
            return
        target_id = target_user_obj.id
        db.get_or_create_user(target_id, target_user_obj.username or target_user_obj.first_name)
        amount_str = parts[1]
    else:
        # /pay [user_id] 100
        parts = message.text.split()
        if len(parts) != 3:
            await message.answer("Использование: <code>/pay [user_id] 100</code>\n"
                                  "или ответь на сообщение получателя командой <code>/pay 100</code>")
            return
        try:
            target_id = int(parts[1])
        except ValueError:
            await message.answer("user_id должен быть числом.")
            return
        if not db.user_exists(target_id):
            await message.answer("Такого игрока нет в базе (он ещё не запускал /start у бота).")
            return
        amount_str = parts[2]

    if target_id == message.from_user.id:
        await message.answer("Нельзя перевести тенге самому себе 🙂")
        return

    try:
        amount = int(amount_str)
    except ValueError:
        await message.answer("Сумма должна быть числом.")
        return
    if amount < MIN_TRANSFER:
        await message.answer(f"Минимальная сумма перевода — {MIN_TRANSFER} тенге.")
        return
    if amount > sender["balance"]:
        await message.answer("У тебя не хватает тенге для этого перевода.")
        return

    db.change_balance(message.from_user.id, -amount)
    db.admin_add_balance(target_id, amount)  # credit without touching games_played stats

    sender_name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name

    if message.chat.type == "private":
        # DM-to-DM transfer: fine to notify the recipient in their own chat with the bot
        await message.answer(f"✅ Переведено <b>{amount}</b> тенге ₸")
        try:
            await bot.send_message(target_id, f"💸 {sender_name} перевёл(а) тебе <b>{amount}</b> тенге ₸")
        except Exception:
            pass  # recipient may have blocked the bot
    else:
        # Group transfer: keep the notification inside the group, never DM the recipient
        try:
            recipient_chat = await bot.get_chat(target_id)
            recipient_name = f"@{recipient_chat.username}" if recipient_chat.username else recipient_chat.first_name
        except Exception:
            recipient_name = str(target_id)
        await message.answer(f"✅ {sender_name} перевёл(а) {recipient_name} <b>{amount}</b> тенге ₸")


# ---------------- Group features: duels & group leaderboard ----------------

@dp.message(Command("grouptop"))
async def cmd_grouptop(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Эта команда работает только в групповых чатах.")
        return
    staff_ids = set(db.list_admins()) | ({OWNER_ID} if OWNER_ID else set())
    rows = db.get_group_top(message.chat.id, staff_ids, 10)
    if not rows:
        await message.answer("В этом чате пока никто не играл.")
        return
    text = "🏆 <b>Топ игроков этого чата:</b>\n\n"
    for i, r in enumerate(rows, 1):
        name = r["username"] or "Аноним"
        text += f"{i}. {name} — {r['balance']} ₸\n"
    await message.answer(text)


def duel_keyboard(challenger_id: int, target_id: int, amount: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"duel_accept:{challenger_id}:{target_id}:{amount}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"duel_decline:{challenger_id}:{target_id}"),
    ]])


@dp.message(Command("duel"))
async def cmd_duel(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Дуэли доступны только в групповых чатах.")
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.answer("Использование: ответь (reply) на сообщение соперника командой "
                              "<code>/duel 100</code>")
        return
    opponent = message.reply_to_message.from_user
    if opponent.is_bot:
        await message.answer("Нельзя вызвать бота на дуэль.")
        return
    if opponent.id == message.from_user.id:
        await message.answer("Нельзя вызвать самого себя на дуэль 🙂")
        return

    challenger = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    db.get_or_create_user(opponent.id, opponent.username or opponent.first_name)

    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/duel 100</code> (в ответ на сообщение соперника)")
        return
    amount = parse_bet(parts[1], challenger["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return

    await message.answer(
        f"⚔️ <b>Вызов на дуэль!</b>\n"
        f"{message.from_user.first_name} вызывает {opponent.first_name} на ставку <b>{amount}</b> тенге!\n"
        f"Победитель определяется подбрасыванием монетки.",
        reply_markup=duel_keyboard(message.from_user.id, opponent.id, amount),
    )


@dp.callback_query(F.data.startswith("duel_accept:"))
async def cb_duel_accept(callback: CallbackQuery):
    _, challenger_id, target_id, amount = callback.data.split(":")
    challenger_id, target_id, amount = int(challenger_id), int(target_id), int(amount)
    if callback.from_user.id != target_id:
        await callback.answer("Только вызванный игрок может принять дуэль!", show_alert=True)
        return

    challenger_balance = db.get_balance(challenger_id)
    target_balance = db.get_balance(target_id)
    if challenger_balance < amount:
        await callback.message.edit_text("❌ У вызвавшего игрока больше не хватает тенге. Дуэль отменена.")
        await callback.answer()
        return
    if target_balance < amount:
        await callback.answer(f"У тебя не хватает тенге для ставки {amount}!", show_alert=True)
        return

    db.change_balance(challenger_id, -amount)
    db.change_balance(target_id, -amount)
    winner_id = random.choice([challenger_id, target_id])
    loser_id = target_id if winner_id == challenger_id else challenger_id
    db.change_balance(winner_id, amount * 2, won=True)

    try:
        winner_chat = await bot.get_chat(winner_id)
        winner_name = winner_chat.first_name
        loser_chat = await bot.get_chat(loser_id)
        loser_name = loser_chat.first_name
    except Exception:
        winner_name, loser_name = str(winner_id), str(loser_id)

    await callback.message.edit_text(
        f"⚔️ <b>Дуэль завершена!</b>\n🏆 Победитель: {winner_name} (+{amount * 2} ₸)\n😢 Проигравший: {loser_name}"
    )
    await callback.answer("Монетка подброшена!")


@dp.callback_query(F.data.startswith("duel_decline:"))
async def cb_duel_decline(callback: CallbackQuery):
    _, challenger_id, target_id = callback.data.split(":")
    target_id = int(target_id)
    if callback.from_user.id != target_id:
        await callback.answer("Только вызванный игрок может отклонить дуэль!", show_alert=True)
        return
    await callback.message.edit_text("🚫 Дуэль отклонена.")
    await callback.answer()


# ---------------- Owner: economy settings ----------------

@dp.message(Command("economy"))
async def cmd_economy(message: Message):
    if not is_admin(message.from_user.id):
        return
    s = db.get_all_settings()
    await message.answer(
        "⚙️ <b>Настройки экономики</b>\n\n"
        f"Стартовый баланс новых игроков: {s.get('start_balance')}\n"
        f"Ежедневный бонус (база): {s.get('daily_bonus')}\n"
        f"Прибавка бонуса за день серии: {s.get('daily_streak_step')} (макс. {s.get('daily_streak_cap')} дней)\n"
        f"Мин. ставка: {s.get('min_bet')}\n"
        f"Макс. ставка: {s.get('max_bet')}\n"
        f"Длительность раунда лотереи: {s.get('lottery_duration_seconds')} сек.\n"
        f"Комиссия лотереи: {float(s.get('lottery_house_edge', 0)) * 100:.0f}%\n"
        f"Награда /work: {s.get('work_min')}-{s.get('work_max')} тенге\n"
        f"Кулдаун /work: {s.get('work_cooldown_minutes')} мин."
    )


@dp.message(Command("setminbet"))
async def cmd_setminbet(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/setminbet [сумма]</code>")
        return
    db.set_setting("min_bet", int(parts[1]))
    await message.answer(f"✅ Минимальная ставка теперь {parts[1]} тенге.")


@dp.message(Command("setmaxbet"))
async def cmd_setmaxbet(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/setmaxbet [сумма]</code>")
        return
    db.set_setting("max_bet", int(parts[1]))
    await message.answer(f"✅ Максимальная ставка теперь {parts[1]} тенге.")


@dp.message(Command("setdailybonus"))
async def cmd_setdailybonus(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/setdailybonus [сумма]</code> — базовый ежедневный бонус "
                              "(до прибавки за серию).")
        return
    db.set_setting("daily_bonus", int(parts[1]))
    await message.answer(f"✅ Базовый ежедневный бонус теперь {parts[1]} тенге.")


@dp.message(Command("setstartbalance"))
async def cmd_setstartbalance(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/setstartbalance [сумма]</code> — влияет только на "
                              "новых игроков, у существующих баланс не меняется.")
        return
    db.set_setting("start_balance", int(parts[1]))
    await message.answer(f"✅ Стартовый баланс новых игроков теперь {parts[1]} тенге.")


@dp.message(Command("setlotteryduration"))
async def cmd_setlotteryduration(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/setlotteryduration [время]</code>, "
                              "например <code>2m</code>, <code>1h</code>.")
        return
    seconds = parse_duration(parts[1])
    if seconds is None or seconds <= 0:
        await message.answer("Не понял время. Примеры: 30s, 2m, 1h.")
        return
    db.set_setting("lottery_duration_seconds", seconds)
    await message.answer(f"✅ Длительность раунда лотереи теперь {format_duration(seconds)}.")


@dp.message(Command("setlotteryedge"))
async def cmd_setlotteryedge(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    try:
        percent = float(parts[1]) if len(parts) == 2 else None
    except ValueError:
        percent = None
    if percent is None or not (0 <= percent <= 50):
        await message.answer("Использование: <code>/setlotteryedge [проценты]</code>, например "
                              "<code>/setlotteryedge 5</code> (от 0 до 50).")
        return
    db.set_setting("lottery_house_edge", percent / 100)
    await message.answer(f"✅ Комиссия лотереи теперь {percent:.0f}%.")


@dp.message(Command("setworkreward"))
async def cmd_setworkreward(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3 or not (parts[1].isdigit() and parts[2].isdigit()) or int(parts[1]) > int(parts[2]):
        await message.answer("Использование: <code>/setworkreward [мин] [макс]</code>, например "
                              "<code>/setworkreward 50 300</code>.")
        return
    db.set_setting("work_min", int(parts[1]))
    db.set_setting("work_max", int(parts[2]))
    await message.answer(f"✅ Награда за /work теперь от {parts[1]} до {parts[2]} тенге.")


@dp.message(Command("setworkcooldown"))
async def cmd_setworkcooldown(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/setworkcooldown [время]</code>, например "
                              "<code>20m</code>, <code>1h</code>.")
        return
    seconds = parse_duration(parts[1])
    if seconds is None or seconds <= 0:
        await message.answer("Не понял время. Примеры: 10m, 30m, 1h.")
        return
    db.set_setting("work_cooldown_minutes", seconds // 60)
    await message.answer(f"✅ Кулдаун /work теперь {format_duration(seconds)}.")


@dp.message(Command("resetalleconomy"))
async def cmd_resetalleconomy(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or parts[1] != "CONFIRM":
        await message.answer("⚠️ Это сбросит баланс <b>ВСЕХ</b> игроков до стартового значения. "
                              "Необратимо. Чтобы подтвердить, напиши:\n"
                              "<code>/resetalleconomy CONFIRM</code>")
        return
    start = int(db.get_setting("start_balance", db.START_BALANCE))
    db.reset_all_balances(start)
    await message.answer(f"✅ Баланс всех игроков сброшен до {start} тенге.")


# ---------------- Owner: manage admins ----------------

@dp.message(Command("addadmin"))
async def cmd_addadmin(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/addadmin [user_id]</code>")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("user_id должен быть числом.")
        return
    db.add_admin(target_id, added_by=message.from_user.id)
    await set_admin_menu(target_id)
    await message.answer(f"✅ <code>{target_id}</code> назначен админом.")
    try:
        await bot.send_message(target_id, "🎉 Тебя назначили админом бота! Доступны /give, /broadcast, /giveaway "
                                           "и другие — загляни в меню команд (кнопка /).")
    except Exception:
        pass


@dp.message(Command("removeadmin"))
async def cmd_removeadmin(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/removeadmin [user_id]</code>")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("user_id должен быть числом.")
        return
    db.remove_admin(target_id)
    await reset_private_menu(target_id)
    await message.answer(f"✅ <code>{target_id}</code> больше не админ.")


@dp.message(Command("admins"))
async def cmd_admins(message: Message):
    if not is_admin(message.from_user.id):
        return
    ids = db.list_admins()

    text = "👑 <b>Владелец</b>\n"
    if OWNER_ID:
        owner_balance = db.get_balance(OWNER_ID) if db.user_exists(OWNER_ID) else "—"
        text += f"{OWNER_ID} (баланс: {owner_balance} ₸)\n\n"
    else:
        text += "не задан\n\n"

    text += "🛡 <b>Админы</b>\n"
    if ids:
        for i in ids:
            balance = db.get_balance(i) if db.user_exists(i) else "—"
            text += f"— {i} (баланс: {balance} ₸)\n"
    else:
        text += "пока нет.\n"

    text += "\nℹ️ Балансы владельца и админов ведутся отдельно от рейтинга игроков — " \
            "они не отображаются в /top и /grouptop."
    await message.answer(text)


async def main():
    db.init_db()
    await setup_default_menus()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
