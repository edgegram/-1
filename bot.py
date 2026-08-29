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
    FSInputFile, ChatPermissions, ChatMemberUpdated,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import database as db
import games

logging.basicConfig(level=logging.INFO)

PROPERTY_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "property")
CARS_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "cars")
BUSINESS_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "business")

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

# active mines games: user_id -> dict(bet, mines, mine_positions, revealed, multiplier, chat_id, message_id)
active_mines = {}

# active blackjack games: user_id -> dict(bet, player, dealer, chat_id, message_id)
active_blackjack = {}

# active lottery pools: chat_id -> dict(tickets: list[(user_id, amount)], message_id)
active_lottery = {}

# crypto: symbol -> max % swing per price-update tick (meme coins are more volatile)
CRYPTO_VOLATILITY = {"BTC": 0.02, "ETH": 0.03, "TON": 0.04, "DOGE": 0.08}
CRYPTO_UPDATE_INTERVAL_SECONDS = 60


# ---------------- Slash-command menu (the list that pops up on "/") ----------------

BASE_COMMANDS = [
    BotCommand(command="start", description="Начать / приветствие"),
    BotCommand(command="balance", description="Баланс"),
    BotCommand(command="daily", description="Ежедневный бонус"),
    BotCommand(command="work", description="Поработать за деньги"),
    BotCommand(command="pay", description="Перевести баланс игроку"),
    BotCommand(command="bills", description="Мои счета (коммуналка/налог)"),
    BotCommand(command="paybills", description="Оплатить счета"),
    BotCommand(command="deposit", description="Открыть вклад"),
    BotCommand(command="deposits", description="Мои вклады"),
    BotCommand(command="withdraw", description="Забрать вклад"),
    BotCommand(command="loan", description="Взять кредит"),
    BotCommand(command="myloan", description="Мой кредит"),
    BotCommand(command="repayloan", description="Погасить кредит"),
    BotCommand(command="crypto", description="Курсы криптовалют"),
    BotCommand(command="buycrypto", description="Купить крипту"),
    BotCommand(command="sellcrypto", description="Продать крипту"),
    BotCommand(command="mycrypto", description="Мой крипто-портфель"),
    BotCommand(command="mystats", description="Моя статистика"),
    BotCommand(command="nik", description="Установить ник"),
    BotCommand(command="forbes", description="Топ 100 по капиталу (всё имущество)"),
    BotCommand(command="richlist", description="Рейтинг всех игроков"),
    BotCommand(command="tops", description="Топ игроков"),
    BotCommand(command="myid", description="Мой Telegram ID"),
    BotCommand(command="channel", description="Наш канал"),
    BotCommand(command="currencies", description="Список доступных валют"),
    BotCommand(command="coinflip", description="Орёл/решка"),
    BotCommand(command="dice", description="Кости"),
    BotCommand(command="slots", description="Слот-машина"),
    BotCommand(command="roulette", description="Рулетка"),
    BotCommand(command="wheel", description="Колесо фортуны"),
    BotCommand(command="mines", description="Мины"),
    BotCommand(command="blackjack", description="Блэкджек (21)"),
    BotCommand(command="trade", description="Трейд верх/вниз"),
    BotCommand(command="crash", description="Крэш-график"),
    BotCommand(command="lottery", description="Лотерея (общий банк)"),
    BotCommand(command="business", description="Магазин бизнесов"),
    BotCommand(command="mybiz", description="Мои бизнесы"),
    BotCommand(command="collect", description="Собрать доход с бизнесов"),
    BotCommand(command="property", description="Каталог имущества"),
    BotCommand(command="myproperty", description="Моё имущество"),
    BotCommand(command="cars", description="Гараж — категории машин"),
    BotCommand(command="mycars", description="Мой гараж"),
]

GROUP_ONLY_COMMANDS = [
    BotCommand(command="duel", description="Вызвать на дуэль (ответом)"),
    BotCommand(command="grouptop", description="Топ игроков этого чата"),
]

ADMIN_COMMANDS = [
    BotCommand(command="botban", description="[admin] Заблокировать в боте (не в группе)"),
    BotCommand(command="unbotban", description="[admin] Разблокировать в боте"),
    BotCommand(command="userinfo", description="[admin] Инфо об игроке"),
    BotCommand(command="stats", description="[admin] Статистика бота"),
    BotCommand(command="groupinfo", description="[admin] Инфо о группе"),
    BotCommand(command="broadcast", description="[admin] Рассылка всем игрокам"),
    BotCommand(command="economy", description="[admin] Настройки экономики"),
    BotCommand(command="admins", description="[admin] Список админов и владельца"),
    BotCommand(command="mute", description="[admin] Замутить в группе"),
    BotCommand(command="unmute", description="[admin] Размутить в группе"),
    BotCommand(command="warn", description="[admin] Выдать предупреждение"),
    BotCommand(command="unwarn", description="[admin] Снять предупреждение"),
    BotCommand(command="warns", description="[admin] Список предупреждений"),
    BotCommand(command="ban", description="[admin] Забанить из группы"),
    BotCommand(command="unban", description="[admin] Разбанить в группе"),
    BotCommand(command="kick", description="[admin] Кикнуть из группы"),
]

OWNER_COMMANDS = [
    BotCommand(command="give", description="[owner] Начислить/списать баланс"),
    BotCommand(command="take", description="[owner] Списать баланс (штраф)"),
    BotCommand(command="setbalance", description="[owner] Жёстко выставить баланс"),
    BotCommand(command="resetuser", description="[owner] Сбросить баланс игрока"),
    BotCommand(command="addadmin", description="[owner] Назначить админа"),
    BotCommand(command="removeadmin", description="[owner] Снять админа"),
    BotCommand(command="setminbet", description="[owner] Мин. ставка"),
    BotCommand(command="setmaxbet", description="[owner] Макс. ставка"),
    BotCommand(command="setdailybonus", description="[owner] Базовый дневной бонус"),
    BotCommand(command="setstartbalance", description="[owner] Стартовый баланс новых игроков"),
    BotCommand(command="setcurrency", description="[owner] Сменить валюту"),
    BotCommand(command="setlotteryduration", description="[owner] Длительность раунда лотереи"),
    BotCommand(command="setlotteryedge", description="[owner] Комиссия лотереи"),
    BotCommand(command="setcoinflipedge", description="[owner] Хаус-эдж coinflip/dice"),
    BotCommand(command="setbetlimit", description="[owner] Лимит ставки как % от баланса"),
    BotCommand(command="setmaxpayout", description="[owner] Макс. выигрыш за раунд"),
    BotCommand(command="setbizcap", description="[owner] Лимит одного бизнеса в руки"),
    BotCommand(command="setworkreward", description="[owner] Награда за /work"),
    BotCommand(command="setworkcooldown", description="[owner] Кулдаун /work"),
    BotCommand(command="settaxrate", description="[owner] Ставка коммуналки"),
    BotCommand(command="setcartaxrate", description="[owner] Ставка транспортного налога"),
    BotCommand(command="setbiztax", description="[owner] Налог на бизнес"),
    BotCommand(command="setbillsthreshold", description="[owner] Порог блокировки при долге"),
    BotCommand(command="setloanmax", description="[owner] Макс. сумма кредита"),
    BotCommand(command="setloanrate", description="[owner] Ставка по кредиту"),
    BotCommand(command="setwarnthreshold", description="[owner] Лимит предупреждений"),
    BotCommand(command="setwarnaction", description="[owner] Санкция за лимит варнов"),
    BotCommand(command="setmutetime", description="[owner] Мут по умолчанию"),
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


async def is_telegram_group_admin(chat_id: int, user_id: int) -> bool:
    """Checks the user's REAL Telegram admin status in this specific group (creator/administrator),
    independent of the bot's own admin list. Used to gate moderation commands so any group's own
    admins can moderate their chat without needing to be added as a bot admin."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def can_moderate(message: Message) -> bool:
    """True if the caller may use /mute, /warn, /ban etc. — either a bot-level admin/owner,
    or a real Telegram admin of this specific group."""
    if is_admin(message.from_user.id):
        return True
    return await is_telegram_group_admin(message.chat.id, message.from_user.id)


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


# ---------------- Currency system (30 selectable currencies) ----------------

CURRENCIES = {
    "RUB": "₽", "USD": "$", "EUR": "€", "GBP": "£", "BTC": "₿",
    "CENT": "¢", "JPY": "¥", "KZT": "₸", "UAH": "₴", "INR": "₹",
    "KRW": "₩", "THB": "฿", "VND": "₫", "PHP": "₱", "TRY": "₺",
    "ILS": "₪", "PLN": "zł", "CZK": "Kč", "HUF": "Ft", "SEK": "kr",
    "CHF": "Fr", "BRL": "R$", "ARS": "$", "ZAR": "R", "NGN": "₦",
    "GHS": "₵", "AZN": "₼", "GEL": "₾", "AMD": "֏", "ETH": "Ξ",
}


def cur() -> str:
    """Current currency symbol, owner-selectable via /setcurrency."""
    code = db.get_setting("currency_code", "RUB")
    return CURRENCIES.get(code, "₸")


def cur_code() -> str:
    return db.get_setting("currency_code", "RUB")


def fmt(n) -> str:
    """Formats a number with dots as thousand separators: 1000 -> '1.000', 1000000 -> '1.000.000'."""
    n = int(n)
    sign = "-" if n < 0 else ""
    return sign + f"{abs(n):,}".replace(",", ".")


def parse_bet(arg: str, balance: int) -> int | None:
    try:
        amount = int(arg)
    except ValueError:
        return None
    min_bet = int(db.get_setting("min_bet", 10))
    max_bet = int(db.get_setting("max_bet", 50000))
    max_percent = float(db.get_setting("max_bet_percent_of_balance", 15))
    table_limit = min(max_bet, int(balance * max_percent / 100))
    if amount < min_bet or amount > table_limit or amount > balance:
        return None
    return amount


def cap_win(amount: int) -> int:
    """Real casinos cap the maximum payout on any single round — applied here so no lucky
    spin turns a modest bet into an instant fortune."""
    cap = int(db.get_setting("max_single_payout", 5_000_000))
    return min(amount, cap)


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
        f"✨ <b>Приветствую вас в симуляторе жизни от minta (maalavo)!</b> ✨\n\n"
        f"Здесь вы сможете воплотить в реальность свои мечты, но только в Telegram.\n\n"
        f"У вас есть возможность приобрести автомобили, самолеты, вертолеты, дома, "
        f"квартиры, бизнесы, а также найти множество увлекательных занятий. "
        f"Кроме того, вы можете играть в наше любимое казино и предаваться азарту.\n\n"
        f"💳 Стартовый баланс: <b>{fmt(user['balance'])}</b> {cur()}\n\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🎮 <b>Казино:</b> /coinflip /dice /slots /roulette /wheel /mines /blackjack /trade /crash /lottery\n"
        f"💰 <b>Экономика:</b> /balance /daily /work /pay /nik /mystats\n"
        f"🏦 <b>Банк:</b> /deposit /loan /crypto\n"
        f"🚗 <b>Имущество:</b> /cars /property /business\n"
        f"🏆 <b>Топы:</b> /tops /forbes /richlist\n"
        f"👥 <b>В группах:</b> /duel — дуэль с другим игроком, /grouptop — топ чата\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"Полный список команд с описанием — по кнопке <b>/</b> рядом с полем ввода.\n\n"
        f"Надеюсь, вам понравится этот бот!\n"
        f"С любовью, mint ❤️",
        reply_markup=channel_keyboard(),
    )


GROUP_WELCOME_TEXT = (
    "✨ <b>Приветствую вас в симуляторе жизни от minta (maalavo)!</b> ✨\n\n"
    "Здесь вы сможете воплотить в реальность свои мечты, но только в Telegram.\n\n"
    "У вас есть возможность приобрести автомобили, самолеты, вертолеты, дома, "
    "квартиры, бизнесы, а также найти множество увлекательных занятий. "
    "Кроме того, вы можете играть в наше любимое казино и предаваться азарту.\n\n"
    "━━━━━━━━━━━━━━━\n"
    "⚔️ /duel [ставка] — вызвать кого-то на дуэль (ответом на его сообщение)\n"
    "🏆 /grouptop — топ игроков этого чата\n"
    "🎰 /coinflip /dice /slots /roulette /wheel /mines /blackjack /trade /crash /lottery — все игры "
    "работают и в группе тоже\n\n"
    "🛡 Если тут есть админы группы — им уже доступна модерация: /mute /warn /ban /kick и т.д. "
    "(права проверяются по реальному статусу админа в Telegram, отдельно настраивать не нужно).\n"
    "Чтобы это работало — дайте боту права администратора с возможностью банить/ограничивать участников.\n"
    "━━━━━━━━━━━━━━━\n\n"
    "Полный список команд — по кнопке <b>/</b> рядом с полем ввода.\n\n"
    "Надеюсь, вам понравится этот бот!\n"
    "С любовью, mint ❤️"
)


@dp.my_chat_member()
async def on_bot_membership_changed(event: ChatMemberUpdated):
    if event.chat.type not in ("group", "supergroup"):
        return
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    if old_status in ("left", "kicked") and new_status in ("member", "administrator"):
        try:
            await bot.send_message(event.chat.id, GROUP_WELCOME_TEXT, reply_markup=channel_keyboard())
        except Exception:
            pass  # bot may lack permission to post, e.g. if added without send rights


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
    await message.answer(f"{cur()} Твой баланс{role}: <b>{fmt(user['balance'])}</b> {cur()}")


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
        f"🎁 Ежедневный бонус получен: <b>+{fmt(bonus)}</b> {cur()}!\n"
        f"🔥 Серия: <b>{streak}</b> {'день' if streak == 1 else 'дней'} подряд\n"
        f"Баланс: <b>{fmt(new_balance)}</b> {cur()}\nПриходи через 24 часа, чтобы не сбить серию."
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

    pay_multiplier = float(db.get_setting("work_pay_multiplier", 1.0))
    job, base_pay = games.do_work()
    reward = int(base_pay * pay_multiplier)

    db.admin_add_balance(uid, reward)
    db.set_last_work(uid, now.isoformat())
    new_balance = db.get_balance(uid)
    await message.answer(
        f"{job}\n💰 Заработал: <b>+{fmt(reward)}</b> {cur()}\nБаланс: <b>{fmt(new_balance)}</b> {cur()}\n"
        f"Следующая работа через {cooldown_minutes} мин."
    )


@dp.message(Command("mystats"))
async def cmd_mystats(message: Message):
    row = db.get_user_row(message.from_user.id)
    if row is None:
        row = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    await message.answer(
        f"📊 <b>Твоя статистика</b>\n\n"
        f"Баланс: <b>{fmt(row['balance'])}</b> {cur()}\n"
        f"Сыграно игр: {row['games_played']}\n"
        f"Всего выиграно: {row['total_won']}\n"
        f"Всего проиграно: {row['total_lost']}\n"
        f"🔥 Серия ежедневных бонусов: {db.get_streak(message.from_user.id)}"
    )


@dp.message(Command("nik", "nick"))
async def cmd_nik(message: Message):
    db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    nickname = message.text.partition(" ")[2].strip()
    if not nickname:
        current = db.get_nickname(message.from_user.id)
        await message.answer(
            f"Твой ник сейчас: <b>{current or '— не задан (используется @username)'}</b>\n\n"
            f"Использование: <code>/nik [новый ник]</code>\nСбросить: <code>/nik сброс</code>"
        )
        return
    if nickname.lower() in ("сброс", "reset", "off", "-"):
        db.set_nickname(message.from_user.id, None)
        await message.answer("✅ Ник сброшен, снова показывается @username.")
        return
    nickname = nickname.replace("<", "").replace(">", "").replace("&", "").replace("\n", " ")
    if len(nickname) > 24:
        await message.answer("Слишком длинный ник — максимум 24 символа.")
        return
    if len(nickname) < 2:
        await message.answer("Слишком короткий ник — минимум 2 символа.")
        return
    db.set_nickname(message.from_user.id, nickname)
    await message.answer(f"✅ Ник установлен: <b>{nickname}</b>\nБудет показываться в /tops, /grouptop, /forbes.")


def mention_link(user_id: int, name: str) -> str:
    """Clickable link straight to the player's profile, even without a public @username."""
    safe_name = name.replace("<", "").replace(">", "").replace("&", "")
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'


def display_name(row) -> str:
    """Prefers the player's custom nickname (/nik) over their Telegram username."""
    if row["nickname"]:
        return row["nickname"]
    return row["username"] or "Аноним"


@dp.message(Command("tops"))
async def cmd_tops(message: Message):
    parts = message.text.split()
    show_full = len(parts) == 2 and parts[1].lower() == "full"

    if show_full:
        if not is_admin(message.from_user.id):
            await message.answer("Полный топ (с админами/владельцем) доступен только админам. "
                                  "Обычный рейтинг — просто /tops.")
            return
        staff_ids = set(db.list_admins()) | ({OWNER_ID} if OWNER_ID else set())
        rows = db.top_players_all(30)
        if not rows:
            await message.answer("Пока нет игроков.")
            return
        text = "🏆 <b>Полный топ (игроки + стафф):</b>\n\n"
        for i, r in enumerate(rows, 1):
            name = display_name(r)
            tag = ""
            if r["user_id"] == OWNER_ID:
                tag = " 👑(владелец)"
            elif r["user_id"] in staff_ids:
                tag = " 🛡(админ)"
            text += f"{i}. {mention_link(r['user_id'], name)} — {fmt(r['balance'])} {cur()}{tag}\n"
        await message.answer(text, disable_web_page_preview=True)
        return

    staff_ids = set(db.list_admins()) | ({OWNER_ID} if OWNER_ID else set())
    rows = db.top_players_excluding(staff_ids, 10)
    if not rows:
        await message.answer("Пока нет игроков.")
        return
    text = "🏆 <b>Топ игроков:</b>\n\n"
    for i, r in enumerate(rows, 1):
        name = display_name(r)
        text += f"{i}. {mention_link(r['user_id'], name)} — {fmt(r['balance'])} {cur()}\n"
    await message.answer(text, disable_web_page_preview=True)


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
    edge = float(db.get_setting("coinflip_dice_house_edge", 0.05))
    if win:
        profit = int(amount * (1 - edge))
        db.change_balance(user["user_id"], profit, won=True)
        await message.answer(f"🪙 Выпало: <b>{label}</b>\n✅ Ты выиграл <b>{fmt(profit)}</b> {cur()}!")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🪙 Выпало: <b>{label}</b>\n❌ Ты проиграл <b>{fmt(amount)}</b> {cur()}.")


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
    edge = float(db.get_setting("coinflip_dice_house_edge", 0.05))
    if win:
        profit = int(amount * (1 - edge))
        db.change_balance(user["user_id"], profit, won=True)
        await message.answer(f"🎲 Выпало: <b>{roll}</b>\n✅ Ты выиграл <b>{fmt(profit)}</b> {cur()}!")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎲 Выпало: <b>{roll}</b>\n❌ Ты проиграл <b>{fmt(amount)}</b> {cur()}.")


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
        winnings = cap_win(int(amount * multiplier))
        db.change_balance(user["user_id"], winnings - amount, won=True)
        await message.answer(f"🎰 [ {reels_text} ]\n✅ Множитель x{multiplier}! Выигрыш: <b>{fmt(winnings)}</b> {cur()}")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎰 [ {reels_text} ]\n❌ Проигрыш <b>{fmt(amount)}</b> {cur()}.")


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
        winnings = cap_win(int(amount * multiplier))
        db.change_balance(user["user_id"], winnings - amount, won=True)
        await message.answer(f"🎡 Выпало: <b>{number}</b> ({color_ru})\n"
                              f"✅ Выигрыш x{multiplier}: <b>{fmt(winnings)}</b> {cur()}!")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎡 Выпало: <b>{number}</b> ({color_ru})\n"
                              f"❌ Проигрыш <b>{fmt(amount)}</b> {cur()}.")


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
        winnings = cap_win(int(amount * multiplier))
        db.change_balance(user["user_id"], winnings - amount, won=(multiplier > 1))
        await message.answer(f"🎡 Колесо остановилось на <b>x{multiplier}</b>!\nВыигрыш: <b>{fmt(winnings)}</b> {cur()}")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎡 Колесо остановилось на <b>x0</b>...\n❌ Проигрыш <b>{fmt(amount)}</b> {cur()}.")


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
    payout = cap_win(int(pot * (1 - house_edge)))
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
        f"🎟 <b>Лотерея завершена!</b>\nУчастников: {participants}\nБанк: {fmt(pot)} {cur()}\n"
        f"🏆 Победитель: {winner_name}\nВыигрыш: <b>{fmt(payout)}</b> {cur()}"
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
            f"🎟 <b>Новый розыгрыш лотереи начат!</b>\nТвой билет: {fmt(amount)} {cur()}\nБанк: {fmt(pot)} {cur()}\n"
            f"Покупай билет командой <code>/lottery [сумма]</code>. Итоги через {duration} сек."
        )
        asyncio.create_task(_lottery_timer(chat_id, duration))
    else:
        await message.answer(f"🎟 Билет куплен: {fmt(amount)} {cur()}. Текущий банк: <b>{fmt(pot)}</b> {cur()}")


async def _lottery_timer(chat_id: int, duration: int):
    await asyncio.sleep(duration)
    await finish_lottery(chat_id)


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
        f"💣 <b>Мины!</b>\nСтавка: {fmt(amount)} | Мин на поле: {mines_count}\nМножитель: <b>x1.0</b>\n"
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
        winnings = cap_win(int(state["bet"] * state["multiplier"]))
        db.change_balance(uid, winnings, won=True)
        active_mines.pop(uid, None)
        await bot.edit_message_text(
            chat_id=state["chat_id"], message_id=state["message_id"],
            text=f"🏆 <b>Все безопасные клетки открыты!</b>\nВыигрыш x{state['multiplier']}: <b>{fmt(winnings)}</b> {cur()}",
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
    winnings = cap_win(int(state["bet"] * state["multiplier"]))
    db.change_balance(uid, winnings, won=True)
    active_mines.pop(uid, None)
    await bot.edit_message_text(
        chat_id=state["chat_id"], message_id=state["message_id"],
        text=f"✅ <b>Забрал на x{state['multiplier']}!</b>\nВыигрыш: <b>{fmt(winnings)}</b> {cur()}",
    )
    await callback.answer("Забрал выигрыш!")


@dp.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


# ---------------- Трейд верх/вниз ----------------

@dp.message(Command("trade"))
async def cmd_trade(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    aliases = {"up": "up", "вверх": "up", "down": "down", "вниз": "down"}
    if len(parts) != 3 or parts[2].lower() not in aliases:
        await message.answer("Использование: <code>/trade 100 up</code> (up/down или вверх/вниз) — "
                              "угадай, куда пойдёт цена дальше")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return
    guess = aliases[parts[2].lower()]
    price_before, price_after, outcome = games.play_trade(guess)
    arrow = "📈" if price_after > price_before else ("📉" if price_after < price_before else "➡️")
    text = f"Цена входа: <b>{price_before}</b> {cur()}\nЦена выхода: <b>{price_after}</b> {cur()} {arrow}\n\n"
    if outcome == "win":
        db.change_balance(user["user_id"], amount, won=True)
        await message.answer(text + f"✅ Угадал направление! Выигрыш: <b>{fmt(amount)}</b> {cur()}")
    elif outcome == "lose":
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(text + f"❌ Не угадал. Проигрыш <b>{fmt(amount)}</b> {cur()}.")
    else:
        await message.answer(text + "🤝 Цена не изменилась — ставка возвращена.")


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
        winnings = cap_win(int(bet * 2.5))
        db.change_balance(uid, winnings, won=True)
        result = f"🏆 Блэкджек! Выигрыш x2.5: <b>{fmt(winnings)}</b> {cur()}"
    elif dealer_val > 21 or player_val > dealer_val:
        winnings = cap_win(bet * 2)
        db.change_balance(uid, winnings, won=True)
        result = f"✅ Победа! Выигрыш: <b>{fmt(winnings)}</b> {cur()}"
    elif player_val == dealer_val:
        db.change_balance(uid, bet)  # push, return the bet
        result = "🤝 Ничья, ставка возвращена."
    else:
        result = f"❌ Дилер сильнее. Проигрыш <b>{bet}</b> {cur()}."

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
    if len(parts) not in (2, 3):
        await message.answer("Использование: <code>/crash 100</code> или с автозабором: "
                              "<code>/crash 100 2.5</code> — заберёт сам на указанном множителе.")
        return
    amount = parse_bet(parts[1], user["balance"])
    if amount is None:
        await message.answer("Некорректная ставка (проверь баланс и сумму).")
        return
    auto_cashout = None
    if len(parts) == 3:
        try:
            auto_cashout = float(parts[2])
        except ValueError:
            await message.answer("Множитель автозабора должен быть числом, например 2.5.")
            return
        if auto_cashout < 1.01:
            await message.answer("Множитель автозабора должен быть больше 1.01.")
            return

    db.change_balance(uid, -amount)  # hold the bet upfront
    crash_point = games.generate_crash_point()

    intro = f"📈 <b>Крэш-график запущен!</b>\nСтавка: {fmt(amount)}\nМножитель: <b>x1.00</b>\n"
    intro += f"Автозабор на x{auto_cashout}\n\n" if auto_cashout else "\n"
    intro += "Жми «Забрать», пока не рухнуло!"
    sent = await message.answer(intro, reply_markup=crash_keyboard(uid))

    state = {
        "bet": amount,
        "multiplier": 1.0,
        "crash_point": crash_point,
        "cashed_out": False,
        "auto_cashout": auto_cashout,
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

                if state["auto_cashout"] and state["multiplier"] >= state["auto_cashout"] \
                        and state["multiplier"] < state["crash_point"]:
                    state["cashed_out"] = True
                    winnings = cap_win(int(state["bet"] * state["auto_cashout"]))
                    db.change_balance(uid, winnings, won=True)
                    active_crash.pop(uid, None)
                    await bot.edit_message_text(
                        chat_id=state["chat_id"], message_id=state["message_id"],
                        text=(f"🤖 <b>Автозабор на x{state['auto_cashout']}!</b>\n"
                              f"Выигрыш: <b>{fmt(winnings)}</b> {cur()}"),
                    )
                    return

                if state["multiplier"] >= state["crash_point"]:
                    await bot.edit_message_text(
                        chat_id=state["chat_id"],
                        message_id=state["message_id"],
                        text=(f"📉 <b>КРАХ на x{state['crash_point']}!</b>\n"
                              f"Ставка {fmt(state['bet'])} сгорела. Не успел забрать 😢"),
                    )
                    active_crash.pop(uid, None)
                    return
                await bot.edit_message_text(
                    chat_id=state["chat_id"],
                    message_id=state["message_id"],
                    text=(f"📈 <b>Крэш-график летит!</b>\nСтавка: {fmt(state['bet'])}\n"
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
    winnings = cap_win(int(state["bet"] * state["multiplier"]))
    db.change_balance(target_uid, winnings, won=True)
    active_crash.pop(target_uid, None)
    await bot.edit_message_text(
        chat_id=state["chat_id"],
        message_id=state["message_id"],
        text=(f"✅ <b>Забрал на x{state['multiplier']}!</b>\n"
              f"Выигрыш: <b>{fmt(winnings)}</b> {cur()}"),
    )
    await callback.answer("Успел забрать!")


@dp.message(Command("resetuser"))
async def cmd_resetuser(message: Message):
    if not is_owner(message.from_user.id):
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
    await message.answer(f"✅ Баланс игрока <code>{target_id}</code> сброшен до {fmt(start)} {cur()}.")


@dp.message(Command("botban"))
async def cmd_botban(message: Message):
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
        await message.answer("Использование: <code>/botban [user_id]</code> или ответом на сообщение игрока.\n"
                              "Блокирует использование бота (не то же самое, что /ban — тот банит из группы).")
        return
    if is_admin(target_id):
        await message.answer("Нельзя заблокировать админа или владельца.")
        return
    db.get_or_create_user(target_id, str(target_id))
    db.set_banned(target_id, True)
    await message.answer(f"🚫 Игрок <code>{target_id}</code> больше не может пользоваться ботом.")


@dp.message(Command("unbotban"))
async def cmd_unbotban(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/unbotban [user_id]</code>")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("user_id должен быть числом.")
        return
    db.set_banned(target_id, False)
    await message.answer(f"✅ Игрок <code>{target_id}</code> снова может пользоваться ботом.")


# ---------------- Admin: userinfo, bot stats ----------------

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
        f"Баланс: {fmt(row['balance'])} {cur()}\n"
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
        f"Суммарный баланс в экономике: {s['total_balance']} {cur()}\n"
        f"Всего сыграно игр: {s['total_games']}"
    )


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
        f"Их суммарный баланс: {fmt(total_balance)} {cur()}"
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


def format_duration(seconds: int) -> str:
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"{seconds // 86400} д."
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600} ч."
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60} мин."
    return f"{seconds} сек."


# ---------------- Businesses (passive income shop) ----------------

@dp.message(Command("business", "shop"))
async def cmd_business_shop(message: Message):
    parts = message.text.split()
    if len(parts) == 2 and parts[1].isdigit():
        bid = int(parts[1])
        if bid not in games.BUSINESSES:
            await message.answer("Такого бизнеса нет. Список — просто /business.")
            return
        name, price, income, emoji, filename = games.BUSINESSES[bid]
        path = os.path.join(BUSINESS_ASSETS_DIR, filename)
        caption = (f"{emoji} <b>{name}</b>\nЦена: {fmt(price)} {cur()}\nДоход: {fmt(income)} {cur()}/ч\n\n"
                   f"Купить: <code>/buybiz {bid}</code>")
        if os.path.exists(path):
            await message.answer_photo(FSInputFile(path), caption=caption)
        else:
            await message.answer(caption)
        return

    text = "🏪 <b>Магазин бизнесов</b> — пассивный доход в час\n\n"
    for bid, (name, price, income, emoji, _) in games.BUSINESSES.items():
        text += f"{bid}. {emoji} <b>{name}</b> — {fmt(price)} {cur()}, доход {fmt(income)}/ч\n"
    text += (f"\nПосмотреть с фото: <code>/business [id]</code>\n"
             f"Купить: <code>/buybiz [id] [кол-во]</code>\nСвои бизнесы: /mybiz\n"
             f"Собрать доход: /collect (копится максимум {games.BUSINESS_MAX_ACCRUAL_HOURS}ч)")
    await message.answer(text)


@dp.message(Command("buybiz"))
async def cmd_buybiz(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    block_msg = await bills_block_message(user["user_id"])
    if block_msg:
        await message.answer(block_msg)
        return
    parts = message.text.split()
    if len(parts) not in (2, 3):
        await message.answer("Использование: <code>/buybiz [id] [кол-во=1]</code>. Каталог — /business.")
        return
    try:
        bid = int(parts[1])
        qty = int(parts[2]) if len(parts) == 3 else 1
    except ValueError:
        await message.answer("id и количество должны быть числами.")
        return
    if bid not in games.BUSINESSES or qty <= 0:
        await message.answer("Такого бизнеса нет. Смотри каталог: /business.")
        return
    max_qty = int(db.get_setting("business_max_quantity", 10))
    existing = db.get_business_owned(user["user_id"], bid)
    already_owned = existing["quantity"] if existing else 0
    if already_owned + qty > max_qty:
        await message.answer(f"Максимум {max_qty} единиц каждого бизнеса в одни руки "
                              f"(у тебя уже {already_owned}). Реальная экономика — не бесконечный принтер.")
        return
    name, price, income, emoji, filename = games.BUSINESSES[bid]
    total_cost = price * qty
    if total_cost > user["balance"]:
        await message.answer(f"Не хватает средств. Нужно {fmt(total_cost)} {cur()}, у тебя {fmt(user['balance'])} {cur()}.")
        return
    db.change_balance(user["user_id"], -total_cost)
    db.buy_business(user["user_id"], bid, qty, datetime.now(timezone.utc).isoformat())
    path = os.path.join(BUSINESS_ASSETS_DIR, filename)
    caption = f"✅ Куплено: {emoji} {name} ×{qty} за {fmt(total_cost)} {cur()}\nДоход теперь копится — забирай командой /collect."
    if os.path.exists(path):
        await message.answer_photo(FSInputFile(path), caption=caption)
    else:
        await message.answer(caption)


@dp.message(Command("mybiz"))
async def cmd_mybiz(message: Message):
    user_id = message.from_user.id
    db.get_or_create_user(user_id, message.from_user.username or message.from_user.first_name)
    owned = db.get_user_businesses(user_id)
    if not owned:
        await message.answer("У тебя пока нет бизнесов. Загляни в /business.")
        return
    now = datetime.now(timezone.utc)
    text = "🏪 <b>Твои бизнесы</b>\n\n"
    total_pending = 0
    for row in owned:
        bid = row["business_id"]
        if bid not in games.BUSINESSES:
            continue
        name, price, income, emoji, _ = games.BUSINESSES[bid]
        last_collect = datetime.fromisoformat(row["last_collect"]) if row["last_collect"] else now
        hours = min((now - last_collect).total_seconds() / 3600, games.BUSINESS_MAX_ACCRUAL_HOURS)
        pending = int(income * row["quantity"] * hours)
        total_pending += pending
        text += f"{emoji} {name} ×{row['quantity']} — накопилось {fmt(pending)} {cur()}\n"
    text += f"\n💰 Всего к сбору: <b>{fmt(total_pending)}</b> {cur()}\nСобрать: /collect"
    await message.answer(text)


@dp.message(Command("collect"))
async def cmd_collect(message: Message):
    user_id = message.from_user.id
    db.get_or_create_user(user_id, message.from_user.username or message.from_user.first_name)
    owned = db.get_user_businesses(user_id)
    if not owned:
        await message.answer("У тебя пока нет бизнесов. Загляни в /business.")
        return
    now = datetime.now(timezone.utc)
    total_income = 0
    for row in owned:
        bid = row["business_id"]
        if bid not in games.BUSINESSES:
            continue
        _, _, income, _, _ = games.BUSINESSES[bid]
        last_collect = datetime.fromisoformat(row["last_collect"]) if row["last_collect"] else now
        hours = min((now - last_collect).total_seconds() / 3600, games.BUSINESS_MAX_ACCRUAL_HOURS)
        total_income += int(income * row["quantity"] * hours)
        db.set_business_last_collect(user_id, bid, now.isoformat())
    if total_income <= 0:
        await message.answer("Пока нечего собирать — доход ещё не накопился.")
        return
    tax_percent = float(db.get_setting("business_tax_percent", 10))
    tax = int(total_income * tax_percent / 100)
    net_income = total_income - tax
    db.admin_add_balance(user_id, net_income)
    new_balance = db.get_balance(user_id)
    await message.answer(
        f"💰 Собрано: <b>{fmt(total_income)}</b> {cur()}\n"
        f"📉 Налог на бизнес ({tax_percent:.0f}%): -{fmt(tax)} {cur()}\n"
        f"✅ Начислено: <b>+{fmt(net_income)}</b> {cur()}\nБаланс: <b>{fmt(new_balance)}</b> {cur()}"
    )


# ---------------- Property (real estate, aviation & watercraft) ----------------

@dp.message(Command("property", "realestate"))
async def cmd_property_shop(message: Message):
    parts = message.text.split()
    cat_aliases = {
        "apartment": "apartment", "квартиры": "apartment", "квартира": "apartment",
        "house": "house", "дома": "house", "дом": "house",
        "plane": "plane", "самолеты": "plane", "самолёты": "plane", "самолет": "plane",
        "helicopter": "helicopter", "вертолеты": "helicopter", "вертолёты": "helicopter",
        "boat": "boat", "водный": "boat", "яхты": "boat",
    }

    if len(parts) == 2 and parts[1].lower() in cat_aliases:
        cat_key = cat_aliases[parts[1].lower()]
        label, emoji = games.PROPERTY_CATEGORIES[cat_key]
        text = f"{emoji} <b>{label}</b>\n\n"
        for pid, (c, name, price, _) in games.PROPERTY.items():
            if c == cat_key:
                text += f"{pid}. {name} — {fmt(price)} {cur()}\n"
        text += "\nПосмотреть с фото: <code>/property [id]</code>\nКупить: <code>/buyproperty [id]</code>"
        await message.answer(text)
        return

    if len(parts) == 2 and parts[1].isdigit():
        pid = int(parts[1])
        if pid not in games.PROPERTY:
            await message.answer("Такого объекта нет. Список категорий — просто /property.")
            return
        cat_key, name, price, filename = games.PROPERTY[pid]
        label, emoji = games.PROPERTY_CATEGORIES[cat_key]
        path = os.path.join(PROPERTY_ASSETS_DIR, filename)
        caption = (f"{emoji} <b>{name}</b>\nКатегория: {label}\nЦена: {fmt(price)} {cur()}\n\n"
                   f"Купить: <code>/buyproperty {pid}</code>")
        if os.path.exists(path):
            await message.answer_photo(FSInputFile(path), caption=caption)
        else:
            await message.answer(caption)
        return

    text = "🏘 <b>Имущество — категории</b>\n\n"
    for cat_key, (label, emoji) in games.PROPERTY_CATEGORIES.items():
        count = sum(1 for c, *_ in games.PROPERTY.values() if c == cat_key)
        text += f"{emoji} <b>{label}</b> — {count}: <code>/property {cat_key}</code>\n"
    text += "\nСвоё имущество: /myproperty"
    await message.answer(text)


@dp.message(Command("buyproperty"))
async def cmd_buyproperty(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    block_msg = await bills_block_message(user["user_id"])
    if block_msg:
        await message.answer(block_msg)
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/buyproperty [id]</code>. Каталог — /property.")
        return
    pid = int(parts[1])
    if pid not in games.PROPERTY:
        await message.answer("Такого объекта нет. Смотри каталог: /property.")
        return
    cat_key, name, price, filename = games.PROPERTY[pid]
    label, emoji = games.PROPERTY_CATEGORIES[cat_key]
    if price > user["balance"]:
        await message.answer(f"Не хватает средств. Нужно {fmt(price)} {cur()}, у тебя {fmt(user['balance'])} {cur()}.")
        return
    db.change_balance(user["user_id"], -price)
    db.buy_property(user["user_id"], pid, 1)
    path = os.path.join(PROPERTY_ASSETS_DIR, filename)
    caption = f"🎉 Поздравляю с покупкой!\n{emoji} <b>{name}</b> ({label}) теперь твоё."
    if os.path.exists(path):
        await message.answer_photo(FSInputFile(path), caption=caption)
    else:
        await message.answer(caption)


@dp.message(Command("myproperty"))
async def cmd_myproperty(message: Message):
    user_id = message.from_user.id
    db.get_or_create_user(user_id, message.from_user.username or message.from_user.first_name)
    owned = db.get_user_property(user_id)
    if not owned:
        await message.answer("У тебя пока нет имущества. Загляни в /property.")
        return
    text = "🏘 <b>Твоё имущество</b>\n\n"
    total_value = 0
    for row in owned:
        pid = row["property_id"]
        if pid not in games.PROPERTY:
            continue
        cat_key, name, price, _ = games.PROPERTY[pid]
        label, emoji = games.PROPERTY_CATEGORIES[cat_key]
        qty = row["quantity"]
        total_value += price * qty
        text += f"{emoji} {name} ({label})" + (f" ×{qty}" if qty > 1 else "") + "\n"
    text += f"\n💎 Суммарная стоимость: <b>{fmt(total_value)}</b> {cur()}"
    await message.answer(text)


# ---------------- Cars (tiered garage) ----------------


@dp.message(Command("cars"))
async def cmd_cars(message: Message):
    parts = message.text.split()
    tier_aliases = {
        "budget": "budget", "бюджет": "budget",
        "mid": "mid", "средние": "mid", "средний": "mid",
        "comfort": "comfort", "комфорт": "comfort",
        "business": "business", "бизнес": "business",
        "sport": "sport", "спорт": "sport",
        "hyper": "hyper", "гиперкар": "hyper", "гипер": "hyper",
    }

    if len(parts) == 2 and parts[1].lower() in tier_aliases:
        tier_key = tier_aliases[parts[1].lower()]
        label, emoji = games.CAR_TIERS[tier_key]
        text = f"{emoji} <b>Гараж — {label}</b>\n\n"
        for cid, (t, model, price, _) in games.CARS.items():
            if t == tier_key:
                text += f"{cid}. {model} — {fmt(price)} {cur()}\n"
        text += "\nПосмотреть с фото: <code>/car [id]</code>\nКупить: <code>/buycar [id]</code>"
        await message.answer(text)
        return

    text = "🚘 <b>Гараж — категории</b>\n\n"
    for tier_key, (label, emoji) in games.CAR_TIERS.items():
        count = sum(1 for t, *_ in games.CARS.values() if t == tier_key)
        text += f"{emoji} <b>{label}</b> — {count} машин: <code>/cars {tier_key}</code>\n"
    text += "\nСвой гараж: /mycars"
    await message.answer(text)


@dp.message(Command("car"))
async def cmd_car(message: Message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/car [id]</code>. Список категорий — /cars.")
        return
    cid = int(parts[1])
    if cid not in games.CARS:
        await message.answer("Такой машины нет. Смотри /cars.")
        return
    tier_key, model, price, filename = games.CARS[cid]
    label, emoji = games.CAR_TIERS[tier_key]
    path = os.path.join(CARS_ASSETS_DIR, filename)
    caption = (f"{emoji} <b>{model}</b>\nКатегория: {label}\nЦена: {fmt(price)} {cur()}\n\n"
               f"Купить: <code>/buycar {cid}</code>")
    if os.path.exists(path):
        await message.answer_photo(FSInputFile(path), caption=caption)
    else:
        await message.answer(caption)


@dp.message(Command("buycar"))
async def cmd_buycar(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    block_msg = await bills_block_message(user["user_id"])
    if block_msg:
        await message.answer(block_msg)
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/buycar [id]</code>. Каталог — /cars.")
        return
    cid = int(parts[1])
    if cid not in games.CARS:
        await message.answer("Такой машины нет. Смотри /cars.")
        return
    tier_key, model, price, filename = games.CARS[cid]
    label, emoji = games.CAR_TIERS[tier_key]
    if price > user["balance"]:
        await message.answer(f"Не хватает средств. Нужно {fmt(price)} {cur()}, у тебя {fmt(user['balance'])} {cur()}.")
        return
    db.change_balance(user["user_id"], -price)
    db.buy_car(user["user_id"], cid, 1)
    path = os.path.join(CARS_ASSETS_DIR, filename)
    caption = f"🎉 Поздравляю с покупкой!\n{emoji} <b>{model}</b> ({label}) теперь в твоём гараже."
    if os.path.exists(path):
        await message.answer_photo(FSInputFile(path), caption=caption)
    else:
        await message.answer(caption)


@dp.message(Command("mycars"))
async def cmd_mycars(message: Message):
    user_id = message.from_user.id
    db.get_or_create_user(user_id, message.from_user.username or message.from_user.first_name)
    owned = db.get_user_cars(user_id)
    if not owned:
        await message.answer("В твоём гараже пока пусто. Загляни в /cars.")
        return
    text = "🚘 <b>Твой гараж</b>\n\n"
    total_value = 0
    for row in owned:
        cid = row["car_id"]
        if cid not in games.CARS:
            continue
        tier_key, model, price, _ = games.CARS[cid]
        label, emoji = games.CAR_TIERS[tier_key]
        qty = row["quantity"]
        total_value += price * qty
        text += f"{emoji} {model} ({label})" + (f" ×{qty}" if qty > 1 else "") + "\n"
    text += f"\n💎 Суммарная стоимость гаража: <b>{fmt(total_value)}</b> {cur()}"
    await message.answer(text)


# ---------------- Forbes: net worth leaderboard (всё имущество считается) ----------------

def compute_net_worth(user_id: int) -> int:
    """Cash + businesses + property + cars + crypto, minus outstanding loan debt.
    (Unpaid bills aren't subtracted here — /forbes ranks gross wealth, not day-to-day cashflow.)"""
    net = db.get_balance(user_id)

    for row in db.get_user_businesses(user_id):
        bid = row["business_id"]
        if bid in games.BUSINESSES:
            _, price, _, _, _ = games.BUSINESSES[bid]
            net += price * row["quantity"]

    for row in db.get_user_property(user_id):
        pid = row["property_id"]
        if pid in games.PROPERTY:
            _, _, price, _ = games.PROPERTY[pid]
            net += price * row["quantity"]

    for row in db.get_user_cars(user_id):
        cid = row["car_id"]
        if cid in games.CARS:
            _, _, price, _ = games.CARS[cid]
            net += price * row["quantity"]

    for row in db.get_user_crypto_holdings(user_id):
        price_row = db.get_crypto_price(row["symbol"])
        if price_row:
            net += int(row["amount"] * price_row["price"])

    loan = db.get_active_loan(user_id)
    if loan:
        net -= loan["remaining_debt"]

    return net


@dp.message(Command("forbes"))
async def cmd_forbes(message: Message):
    staff_ids = set(db.list_admins()) | ({OWNER_ID} if OWNER_ID else set())
    rows = db.get_all_users_full()
    ranked = []
    for row in rows:
        if row["user_id"] in staff_ids:
            continue  # staff wealth kept out of player rankings, same as /tops
        ranked.append((row, compute_net_worth(row["user_id"])))
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    top = ranked[:100]

    if not top:
        await message.answer("Пока нет игроков.")
        return

    text = "💎 <b>FORBES — топ 100 по капиталу</b>\n<i>(баланс + бизнесы + имущество + машины + крипта − кредиты)</i>\n\n"
    lines = []
    for i, (row, net) in enumerate(top, 1):
        lines.append(f"{i}. {mention_link(row['user_id'], display_name(row))} — {fmt(net)} {cur()}")
    text += "\n".join(lines)

    # Telegram messages cap at 4096 chars — split into chunks if the list is long
    if len(text) <= 4000:
        await message.answer(text, disable_web_page_preview=True)
    else:
        header = "💎 <b>FORBES — топ 100 по капиталу</b>\n\n"
        chunk = header
        for line in lines:
            if len(chunk) + len(line) > 3800:
                await message.answer(chunk, disable_web_page_preview=True)
                chunk = ""
            chunk += line + "\n"
        if chunk:
            await message.answer(chunk, disable_web_page_preview=True)


@dp.message(Command("richlist"))
async def cmd_richlist(message: Message):
    staff_ids = set(db.list_admins()) | ({OWNER_ID} if OWNER_ID else set())
    parts = message.text.split()
    page = 1
    if len(parts) == 2 and parts[1].isdigit():
        page = max(1, int(parts[1]))

    rows = db.get_all_users_full()
    ranked = []
    for row in rows:
        if row["user_id"] in staff_ids:
            continue
        ranked.append((row, compute_net_worth(row["user_id"])))
    ranked.sort(key=lambda pair: pair[1], reverse=True)

    page_size = 20
    total_pages = max(1, (len(ranked) + page_size - 1) // page_size)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    page_rows = ranked[start:start + page_size]

    if not page_rows:
        await message.answer("Пока нет игроков.")
        return

    text = f"📊 <b>Рейтинг всех игроков</b> — от богатых к бедным (стр. {page}/{total_pages})\n\n"
    for i, (row, net) in enumerate(page_rows, start + 1):
        text += f"{i}. {mention_link(row['user_id'], display_name(row))} — {fmt(net)} {cur()}\n"
    if total_pages > 1:
        text += f"\nСледующая страница: <code>/richlist {page + 1}</code>"
    await message.answer(text, disable_web_page_preview=True)


# ---------------- Bills & taxes (коммуналка, транспортный налог) ----------------

def compute_unpaid_bills(user_id: int) -> tuple[int, dict]:
    """Returns (total_unpaid, breakdown) where breakdown has keys 'property' and 'car'."""
    last_paid_str = db.get_last_bills_paid(user_id)
    now = datetime.now(timezone.utc)
    if last_paid_str:
        elapsed_hours = (now - datetime.fromisoformat(last_paid_str)).total_seconds() / 3600
    else:
        elapsed_hours = 0  # first-ever check — nothing owed yet, timer starts now
    hours = min(elapsed_hours, games.BILLS_MAX_ACCRUAL_HOURS)

    property_tax_rate = float(db.get_setting("property_tax_rate", 0.00001))
    car_tax_rate = float(db.get_setting("car_tax_rate", 0.000005))

    property_value = 0
    for row in db.get_user_property(user_id):
        pid = row["property_id"]
        if pid in games.PROPERTY:
            _, _, price, _ = games.PROPERTY[pid]
            property_value += price * row["quantity"]

    car_value = 0
    for row in db.get_user_cars(user_id):
        cid = row["car_id"]
        if cid in games.CARS:
            _, _, price, _ = games.CARS[cid]
            car_value += price * row["quantity"]

    property_bill = int(property_value * property_tax_rate * hours)
    car_bill = int(car_value * car_tax_rate * hours)
    return property_bill + car_bill, {"property": property_bill, "car": car_bill}


async def bills_block_message(user_id: int) -> str | None:
    """Returns a warning string if the user owes too much and should be blocked from
    buying more property/cars/businesses, or None if they're clear to purchase."""
    if db.get_last_bills_paid(user_id) is None:
        return None
    total, _ = compute_unpaid_bills(user_id)
    threshold = int(db.get_setting("bills_block_threshold", 5000))
    if total > threshold:
        return (f"🧾 У тебя накопился долг по счетам: {fmt(total)} {cur()}.\n"
                f"Сначала оплати /paybills, потом покупай новое.")
    return None


@dp.message(Command("bills"))
async def cmd_bills(message: Message):
    user_id = message.from_user.id
    db.get_or_create_user(user_id, message.from_user.username or message.from_user.first_name)
    if db.get_last_bills_paid(user_id) is None:
        db.set_last_bills_paid(user_id, datetime.now(timezone.utc).isoformat())
        await message.answer("💡 Счётчик коммуналки и транспортного налога запущен. "
                              "Первый счёт придёт по мере накопления — проверяй командой /bills.")
        return
    total, breakdown = compute_unpaid_bills(user_id)
    if total <= 0:
        await message.answer("✅ Долгов нет — всё оплачено.")
        return
    await message.answer(
        f"🧾 <b>Счета к оплате</b>\n\n"
        f"🏠 Коммуналка (квартиры/дома/самолёты/вертолёты/водный транспорт): {fmt(breakdown['property'])} {cur()}\n"
        f"🚗 Транспортный налог: {fmt(breakdown['car'])} {cur()}\n\n"
        f"💰 Итого: <b>{fmt(total)}</b> {cur()}\nОплатить: /paybills"
    )


@dp.message(Command("paybills"))
async def cmd_paybills(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    user_id = user["user_id"]
    if db.get_last_bills_paid(user_id) is None:
        db.set_last_bills_paid(user_id, datetime.now(timezone.utc).isoformat())
        await message.answer("Долгов пока нет — счётчик только что запущен.")
        return
    total, breakdown = compute_unpaid_bills(user_id)
    if total <= 0:
        await message.answer("✅ Долгов нет — платить нечего.")
        return
    if total > user["balance"]:
        await message.answer(f"❌ Не хватает средств на оплату счетов.\n"
                              f"Нужно {fmt(total)} {cur()}, у тебя {fmt(user['balance'])} {cur()}.\n"
                              f"Собери доход с бизнесов (/collect) или заработай (/work).")
        return
    db.change_balance(user_id, -total)
    db.set_last_bills_paid(user_id, datetime.now(timezone.utc).isoformat())
    new_balance = db.get_balance(user_id)
    await message.answer(f"✅ Счета оплачены: <b>{fmt(total)}</b> {cur()}\nБаланс: <b>{fmt(new_balance)}</b> {cur()}")


# ---------------- Bank: deposits (вклады) ----------------

DEPOSIT_TERMS = {"1d": (1, "deposit_rate_1d"), "7d": (7, "deposit_rate_7d"), "30d": (30, "deposit_rate_30d")}


@dp.message(Command("deposit"))
async def cmd_deposit(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    if len(parts) != 3 or parts[2].lower() not in DEPOSIT_TERMS:
        rates = ", ".join(f"{k} ({float(db.get_setting(v, 0)) * 100:.0f}%)" for k, v in DEPOSIT_TERMS.items())
        await message.answer(f"Использование: <code>/deposit [сумма] [срок]</code>\nСроки и доходность: {rates}\n"
                              f"Пример: <code>/deposit 10000 7d</code>")
        return
    try:
        amount = int(parts[1])
    except ValueError:
        await message.answer("Сумма должна быть числом.")
        return
    if amount <= 0 or amount > user["balance"]:
        await message.answer("Некорректная сумма (проверь баланс).")
        return
    term_days, rate_key = DEPOSIT_TERMS[parts[2].lower()]
    rate = float(db.get_setting(rate_key, 0))

    db.change_balance(user["user_id"], -amount)
    now = datetime.now(timezone.utc)
    deposit_id = db.create_deposit(user["user_id"], amount, rate, term_days, now.isoformat())
    maturity = now + timedelta(days=term_days)
    payout = int(amount * (1 + rate))
    await message.answer(
        f"🏦 <b>Вклад #{deposit_id} открыт</b>\nСумма: {fmt(amount)} {cur()}\nСтавка: {rate*100:.0f}% за {term_days} дн.\n"
        f"Созреет: {maturity.strftime('%d.%m.%Y %H:%M')} UTC\nК получению при погашении: <b>{fmt(payout)}</b> {cur()}\n\n"
        f"Забрать досрочно можно, но тогда сгорят проценты — вернётся только сумма вклада."
    )


@dp.message(Command("deposits"))
async def cmd_deposits(message: Message):
    user_id = message.from_user.id
    db.get_or_create_user(user_id, message.from_user.username or message.from_user.first_name)
    rows = db.get_active_deposits(user_id)
    if not rows:
        await message.answer("У тебя нет активных вкладов. Открыть: /deposit [сумма] [срок].")
        return
    now = datetime.now(timezone.utc)
    text = "🏦 <b>Твои вклады</b>\n\n"
    for row in rows:
        created = datetime.fromisoformat(row["created_at"])
        maturity = created + timedelta(days=row["term_days"])
        payout = int(row["amount"] * (1 + row["rate"]))
        status = "✅ созрел" if now >= maturity else f"⏳ до {maturity.strftime('%d.%m %H:%M')} UTC"
        text += (f"#{row['id']} — {fmt(row['amount'])} {cur()} → {fmt(payout)} {cur()} "
                 f"({row['rate']*100:.0f}%/{row['term_days']}д) {status}\n")
    text += "\nЗабрать: <code>/withdraw [id]</code>"
    await message.answer(text)


@dp.message(Command("withdraw"))
async def cmd_withdraw(message: Message):
    user_id = message.from_user.id
    db.get_or_create_user(user_id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/withdraw [id]</code>. Список вкладов — /deposits.")
        return
    deposit_id = int(parts[1])
    row = db.get_deposit(deposit_id)
    if row is None or row["user_id"] != user_id or row["withdrawn"]:
        await message.answer("Такого активного вклада нет.")
        return
    created = datetime.fromisoformat(row["created_at"])
    maturity = created + timedelta(days=row["term_days"])
    now = datetime.now(timezone.utc)
    if now >= maturity:
        payout = int(row["amount"] * (1 + row["rate"]))
        db.admin_add_balance(user_id, payout)
        db.mark_deposit_withdrawn(deposit_id)
        await message.answer(f"✅ Вклад #{deposit_id} погашен: <b>{fmt(payout)}</b> {cur()} "
                              f"(сумма + {row['rate']*100:.0f}% процентов)")
    else:
        db.admin_add_balance(user_id, row["amount"])
        db.mark_deposit_withdrawn(deposit_id)
        await message.answer(f"⚠️ Вклад #{deposit_id} закрыт досрочно. Проценты сгорели.\n"
                              f"Возвращено: <b>{fmt(row['amount'])}</b> {cur()}")


# ---------------- Bank: loans (кредиты) ----------------

@dp.message(Command("loan"))
async def cmd_loan(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    user_id = user["user_id"]
    if db.get_active_loan(user_id):
        await message.answer("У тебя уже есть непогашенный кредит. Смотри /myloan.")
        return
    parts = message.text.split()
    max_amount = int(db.get_setting("loan_max_amount", 50000))
    if len(parts) != 2:
        await message.answer(f"Использование: <code>/loan [сумма]</code> (максимум {fmt(max_amount)} {cur()})")
        return
    try:
        amount = int(parts[1])
    except ValueError:
        await message.answer("Сумма должна быть числом.")
        return
    if amount <= 0 or amount > max_amount:
        await message.answer(f"Сумма кредита должна быть от 1 до {fmt(max_amount)} {cur()}.")
        return

    rate = float(db.get_setting("loan_interest_rate", 0.15))
    term_days = int(db.get_setting("loan_term_days", 7))
    remaining_debt = int(amount * (1 + rate))
    now = datetime.now(timezone.utc)
    due_at = now + timedelta(days=term_days)

    db.admin_add_balance(user_id, amount)
    db.create_loan(user_id, amount, remaining_debt, now.isoformat(), due_at.isoformat())
    await message.answer(
        f"💳 <b>Кредит выдан:</b> {fmt(amount)} {cur()}\n"
        f"К возврату: <b>{fmt(remaining_debt)}</b> {cur()} ({rate*100:.0f}% за {term_days} дн.)\n"
        f"Срок: до {due_at.strftime('%d.%m.%Y %H:%M')} UTC\n"
        f"Погасить: /repayloan [сумма]. Просрочка увеличивает долг!"
    )


@dp.message(Command("myloan"))
async def cmd_myloan(message: Message):
    user_id = message.from_user.id
    db.get_or_create_user(user_id, message.from_user.username or message.from_user.first_name)
    loan = db.get_active_loan(user_id)
    if not loan:
        await message.answer("У тебя нет активного кредита. Взять: /loan [сумма].")
        return
    due_at = datetime.fromisoformat(loan["due_at"])
    now = datetime.now(timezone.utc)
    overdue_debt = _apply_loan_penalty_if_overdue(user_id, loan)
    status = "❌ ПРОСРОЧЕН" if now > due_at else f"до {due_at.strftime('%d.%m.%Y %H:%M')} UTC"
    await message.answer(
        f"💳 <b>Твой кредит</b>\nВыдано: {fmt(loan['principal'])} {cur()}\n"
        f"Остаток долга: <b>{fmt(overdue_debt)}</b> {cur()}\nСрок: {status}\n\nПогасить: /repayloan [сумма]"
    )


def _apply_loan_penalty_if_overdue(user_id: int, loan) -> int:
    """If the loan is past its due date, applies daily penalty interest and returns the updated debt."""
    due_at = datetime.fromisoformat(loan["due_at"])
    now = datetime.now(timezone.utc)
    if now <= due_at:
        return loan["remaining_debt"]
    overdue_days = int((now - due_at).total_seconds() / 86400) + 1
    penalty_rate = float(db.get_setting("loan_penalty_rate_per_day", 0.05))
    new_debt = int(loan["remaining_debt"] * (1 + penalty_rate) ** overdue_days)
    db.update_loan_debt(user_id, new_debt)
    return new_debt


@dp.message(Command("repayloan"))
async def cmd_repayloan(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    user_id = user["user_id"]
    loan = db.get_active_loan(user_id)
    if not loan:
        await message.answer("У тебя нет активного кредита.")
        return
    debt = _apply_loan_penalty_if_overdue(user_id, loan)
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer(f"Использование: <code>/repayloan [сумма]</code>. Остаток долга: {fmt(debt)} {cur()}")
        return
    try:
        amount = int(parts[1])
    except ValueError:
        await message.answer("Сумма должна быть числом.")
        return
    if amount <= 0:
        await message.answer("Сумма должна быть положительной.")
        return
    if amount > user["balance"]:
        await message.answer(f"Не хватает средств. У тебя {fmt(user['balance'])} {cur()}.")
        return

    payment = min(amount, debt)
    db.change_balance(user_id, -payment)
    new_debt = debt - payment
    if new_debt <= 0:
        db.close_loan(user_id)
        await message.answer(f"✅ Кредит полностью погашен! Списано {fmt(payment)} {cur()}.")
    else:
        db.update_loan_debt(user_id, new_debt)
        await message.answer(f"✅ Внесено {fmt(payment)} {cur()}. Остаток долга: <b>{fmt(new_debt)}</b> {cur()}")


# ---------------- Crypto ----------------

@dp.message(Command("crypto"))
async def cmd_crypto(message: Message):
    rows = db.get_all_crypto_prices()
    text = "📈 <b>Курсы криптовалют</b>\n\n"
    for row in rows:
        change = ((row["price"] - row["prev_price"]) / row["prev_price"] * 100) if row["prev_price"] else 0
        arrow = "🟢▲" if change > 0 else ("🔴▼" if change < 0 else "⚪")
        text += f"{row['symbol']}: {fmt(round(row['price']))} {cur()} {arrow} {change:+.2f}%\n"
    text += "\nКупить: <code>/buycrypto [монета] [сумма]</code>\nПродать: <code>/sellcrypto [монета] [кол-во|all]</code>\n" \
            "Портфель: /mycrypto"
    await message.answer(text)


@dp.message(Command("buycrypto"))
async def cmd_buycrypto(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/buycrypto BTC 10000</code> — купить крипты на указанную сумму.")
        return
    symbol = parts[1].upper()
    price_row = db.get_crypto_price(symbol)
    if price_row is None:
        await message.answer("Такой монеты нет. Список — /crypto.")
        return
    try:
        spend = int(parts[2])
    except ValueError:
        await message.answer("Сумма должна быть числом.")
        return
    if spend <= 0 or spend > user["balance"]:
        await message.answer("Некорректная сумма (проверь баланс).")
        return
    coin_amount = spend / price_row["price"]
    db.change_balance(user["user_id"], -spend)
    db.upsert_crypto_buy(user["user_id"], symbol, coin_amount, price_row["price"])
    await message.answer(f"✅ Куплено {coin_amount:.6f} {symbol} на {fmt(spend)} {cur()} "
                          f"(курс {fmt(round(price_row['price']))} {cur()})")


@dp.message(Command("sellcrypto"))
async def cmd_sellcrypto(message: Message):
    user_id = message.from_user.id
    db.get_or_create_user(user_id, message.from_user.username or message.from_user.first_name)
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/sellcrypto BTC 0.01</code> или <code>/sellcrypto BTC all</code>")
        return
    symbol = parts[1].upper()
    holding = db.get_crypto_holding(user_id, symbol)
    if holding is None or holding["amount"] <= 0:
        await message.answer("У тебя нет этой монеты. Портфель — /mycrypto.")
        return
    price_row = db.get_crypto_price(symbol)
    if parts[2].lower() == "all":
        sell_amount = holding["amount"]
    else:
        try:
            sell_amount = float(parts[2])
        except ValueError:
            await message.answer("Количество должно быть числом или 'all'.")
            return
    if sell_amount <= 0 or sell_amount > holding["amount"]:
        await message.answer(f"У тебя есть только {holding['amount']:.6f} {symbol}.")
        return
    proceeds = int(sell_amount * price_row["price"])
    db.crypto_sell(user_id, symbol, sell_amount)
    db.admin_add_balance(user_id, proceeds)
    await message.answer(f"✅ Продано {sell_amount:.6f} {symbol} за <b>{fmt(proceeds)}</b> {cur()}")


@dp.message(Command("mycrypto"))
async def cmd_mycrypto(message: Message):
    user_id = message.from_user.id
    db.get_or_create_user(user_id, message.from_user.username or message.from_user.first_name)
    holdings = db.get_user_crypto_holdings(user_id)
    if not holdings:
        await message.answer("У тебя пока нет крипты. Купить: /buycrypto [монета] [сумма].")
        return
    text = "💼 <b>Твой крипто-портфель</b>\n\n"
    total_value = 0
    for row in holdings:
        price_row = db.get_crypto_price(row["symbol"])
        value = row["amount"] * price_row["price"]
        total_value += value
        pnl_percent = ((price_row["price"] - row["avg_price"]) / row["avg_price"] * 100) if row["avg_price"] else 0
        pnl_sign = "🟢+" if pnl_percent >= 0 else "🔴"
        text += (f"{row['symbol']}: {row['amount']:.6f} — {fmt(round(value))} {cur()} "
                 f"({pnl_sign}{pnl_percent:.1f}%)\n")
    text += f"\n💰 Итого: <b>{fmt(round(total_value))}</b> {cur()}"
    await message.answer(text)


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
            await message.answer("Нельзя перевести баланс боту.")
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
        await message.answer("Нельзя перевести баланс самому себе 🙂")
        return

    try:
        amount = int(amount_str)
    except ValueError:
        await message.answer("Сумма должна быть числом.")
        return
    if amount < MIN_TRANSFER:
        await message.answer(f"Минимальная сумма перевода — {MIN_TRANSFER} {cur()}.")
        return
    if amount > sender["balance"]:
        await message.answer("У тебя не хватает средств для этого перевода.")
        return

    db.change_balance(message.from_user.id, -amount)
    db.admin_add_balance(target_id, amount)  # credit without touching games_played stats

    sender_name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name

    if message.chat.type == "private":
        # DM-to-DM transfer: fine to notify the recipient in their own chat with the bot
        await message.answer(f"✅ Переведено <b>{fmt(amount)}</b> {cur()}")
        try:
            await bot.send_message(target_id, f"💸 {sender_name} перевёл(а) тебе <b>{fmt(amount)}</b> {cur()}")
        except Exception:
            pass  # recipient may have blocked the bot
    else:
        # Group transfer: keep the notification inside the group, never DM the recipient
        try:
            recipient_chat = await bot.get_chat(target_id)
            recipient_name = f"@{recipient_chat.username}" if recipient_chat.username else recipient_chat.first_name
        except Exception:
            recipient_name = str(target_id)
        await message.answer(f"✅ {sender_name} перевёл(а) {recipient_name} <b>{fmt(amount)}</b> {cur()}")


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
        name = display_name(r)
        text += f"{i}. {name} — {fmt(r['balance'])} {cur()}\n"
    await message.answer(text)


def duel_keyboard(challenger_id: int, target_id: int, amount: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"duel_accept:{challenger_id}:{target_id}:{fmt(amount)}"),
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
        f"{message.from_user.first_name} вызывает {opponent.first_name} на ставку <b>{fmt(amount)}</b> {cur()}!\n"
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
        await callback.message.edit_text("❌ У вызвавшего игрока больше не хватает средств. Дуэль отменена.")
        await callback.answer()
        return
    if target_balance < amount:
        await callback.answer(f"У тебя не хватает {cur()} для ставки {fmt(amount)}!", show_alert=True)
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
        f"⚔️ <b>Дуэль завершена!</b>\n🏆 Победитель: {winner_name} (+{amount * 2} {cur()})\n😢 Проигравший: {loser_name}"
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
        f"Лимит стола: {s.get('max_bet_percent_of_balance')}% от баланса игрока\n"
        f"Макс. выигрыш за раунд: {fmt(int(s.get('max_single_payout', 0)))} {cur()}\n"
        f"Хаус-эдж coinflip/dice: {float(s.get('coinflip_dice_house_edge', 0)) * 100:.0f}%\n"
        f"Лимит бизнеса одного типа: {s.get('business_max_quantity')} шт.\n"
        f"Длительность раунда лотереи: {s.get('lottery_duration_seconds')} сек.\n"
        f"Комиссия лотереи: {float(s.get('lottery_house_edge', 0)) * 100:.0f}%\n"
        f"Множитель зарплат /work: x{s.get('work_pay_multiplier')}\n"
        f"Кулдаун /work: {s.get('work_cooldown_minutes')} мин.\n"
        f"Коммуналка (имущество): {float(s.get('property_tax_rate', 0)) * 100:.4f}%/час от стоимости\n"
        f"Транспортный налог: {float(s.get('car_tax_rate', 0)) * 100:.4f}%/час от стоимости\n"
        f"Налог на бизнес: {s.get('business_tax_percent')}% с /collect\n"
        f"Порог блокировки при долге: {fmt(int(s.get('bills_block_threshold', 5000)))} {cur()}"
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
    await message.answer(f"✅ Минимальная ставка теперь {parts[1]} {cur()}.")


@dp.message(Command("setmaxbet"))
async def cmd_setmaxbet(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/setmaxbet [сумма]</code>")
        return
    db.set_setting("max_bet", int(parts[1]))
    await message.answer(f"✅ Максимальная ставка теперь {parts[1]} {cur()}.")


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
    await message.answer(f"✅ Базовый ежедневный бонус теперь {parts[1]} {cur()}.")


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
    await message.answer(f"✅ Стартовый баланс новых игроков теперь {parts[1]} {cur()}.")


@dp.message(Command("currencies"))
async def cmd_currencies(message: Message):
    lines = [f"{code} {symbol}" for code, symbol in CURRENCIES.items()]
    columns = "\n".join(lines)
    await message.answer(f"💱 <b>Доступные валюты</b> (сейчас: {cur_code()} {cur()})\n\n{columns}\n\n"
                          f"Владелец меняет командой <code>/setcurrency [код]</code>, например "
                          f"<code>/setcurrency USD</code>.")


@dp.message(Command("setcurrency"))
async def cmd_setcurrency(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or parts[1].upper() not in CURRENCIES:
        await message.answer("Использование: <code>/setcurrency [код]</code>. Список кодов — /currencies.")
        return
    code = parts[1].upper()
    db.set_setting("currency_code", code)
    await message.answer(f"✅ Валюта теперь {code} {CURRENCIES[code]}")


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


@dp.message(Command("setcoinflipedge"))
async def cmd_setcoinflipedge(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    try:
        percent = float(parts[1]) if len(parts) == 2 else None
    except ValueError:
        percent = None
    if percent is None or not (0 <= percent <= 50):
        await message.answer("Использование: <code>/setcoinflipedge [проценты]</code>, например "
                              "<code>/setcoinflipedge 5</code> (0-50) — хаус-эдж для /coinflip и /dice.")
        return
    db.set_setting("coinflip_dice_house_edge", percent / 100)
    await message.answer(f"✅ Хаус-эдж /coinflip и /dice теперь {percent:.0f}%.")


@dp.message(Command("setbetlimit"))
async def cmd_setbetlimit(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    try:
        percent = float(parts[1]) if len(parts) == 2 else None
    except ValueError:
        percent = None
    if percent is None or not (0 < percent <= 100):
        await message.answer("Использование: <code>/setbetlimit [проценты]</code>, например "
                              "<code>/setbetlimit 15</code> — максимальная ставка как % от баланса игрока "
                              "(лимит стола, как в реальном казино). Итоговый лимит — минимум из этого и /setmaxbet.")
        return
    db.set_setting("max_bet_percent_of_balance", percent)
    await message.answer(f"✅ Лимит ставки теперь {percent:.0f}% от баланса (плюс потолок /setmaxbet).")


@dp.message(Command("setmaxpayout"))
async def cmd_setmaxpayout(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/setmaxpayout [сумма]</code> — максимальный выигрыш "
                              "за один раунд в любой игре (как лимит выплат в реальном казино).")
        return
    db.set_setting("max_single_payout", int(parts[1]))
    await message.answer(f"✅ Максимальный выигрыш за раунд теперь {fmt(int(parts[1]))} {cur()}.")


@dp.message(Command("setbizcap"))
async def cmd_setbizcap(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) < 1:
        await message.answer("Использование: <code>/setbizcap [число]</code> — максимум единиц одного "
                              "бизнеса в одни руки.")
        return
    db.set_setting("business_max_quantity", int(parts[1]))
    await message.answer(f"✅ Лимит бизнеса одного типа теперь {parts[1]} шт.")


@dp.message(Command("setworkreward"))
async def cmd_setworkreward(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    try:
        multiplier = float(parts[1]) if len(parts) == 2 else None
    except ValueError:
        multiplier = None
    if multiplier is None or multiplier <= 0:
        await message.answer("Использование: <code>/setworkreward [множитель]</code> — масштабирует "
                              "зарплату всех профессий сразу, например <code>/setworkreward 1.5</code> "
                              "(+50% ко всем зарплатам).")
        return
    db.set_setting("work_pay_multiplier", multiplier)
    await message.answer(f"✅ Множитель зарплат /work теперь x{multiplier}.")


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


@dp.message(Command("settaxrate"))
async def cmd_settaxrate(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    try:
        percent = float(parts[1]) if len(parts) == 2 else None
    except ValueError:
        percent = None
    if percent is None or percent < 0:
        await message.answer("Использование: <code>/settaxrate [проценты в час от стоимости]</code>, "
                              "например <code>/settaxrate 0.001</code> (коммуналка за квартиры/дома/самолёты/"
                              "вертолёты/водный транспорт).")
        return
    db.set_setting("property_tax_rate", percent / 100)
    await message.answer(f"✅ Ставка коммуналки теперь {percent}%/час от стоимости имущества.")


@dp.message(Command("setcartaxrate"))
async def cmd_setcartaxrate(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    try:
        percent = float(parts[1]) if len(parts) == 2 else None
    except ValueError:
        percent = None
    if percent is None or percent < 0:
        await message.answer("Использование: <code>/setcartaxrate [проценты в час от стоимости]</code>, "
                              "например <code>/setcartaxrate 0.0005</code> (транспортный налог).")
        return
    db.set_setting("car_tax_rate", percent / 100)
    await message.answer(f"✅ Ставка транспортного налога теперь {percent}%/час от стоимости машины.")


@dp.message(Command("setbiztax"))
async def cmd_setbiztax(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    try:
        percent = float(parts[1]) if len(parts) == 2 else None
    except ValueError:
        percent = None
    if percent is None or not (0 <= percent <= 90):
        await message.answer("Использование: <code>/setbiztax [проценты]</code>, например "
                              "<code>/setbiztax 10</code> (0-90) — налог с дохода при /collect.")
        return
    db.set_setting("business_tax_percent", percent)
    await message.answer(f"✅ Налог на бизнес теперь {percent:.0f}% с каждого /collect.")


@dp.message(Command("setbillsthreshold"))
async def cmd_setbillsthreshold(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/setbillsthreshold [сумма]</code> — при каком долге "
                              "по счетам блокируются новые покупки имущества/машин/бизнесов.")
        return
    db.set_setting("bills_block_threshold", int(parts[1]))
    await message.answer(f"✅ Порог блокировки покупок при долге теперь {parts[1]} {cur()}.")


@dp.message(Command("setloanmax"))
async def cmd_setloanmax(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: <code>/setloanmax [сумма]</code>")
        return
    db.set_setting("loan_max_amount", int(parts[1]))
    await message.answer(f"✅ Максимальный кредит теперь {fmt(int(parts[1]))} {cur()}.")


@dp.message(Command("setloanrate"))
async def cmd_setloanrate(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    try:
        percent = float(parts[1]) if len(parts) == 2 else None
    except ValueError:
        percent = None
    if percent is None or percent < 0:
        await message.answer("Использование: <code>/setloanrate [проценты]</code>, например "
                              "<code>/setloanrate 15</code> — ставка по кредиту за весь срок.")
        return
    db.set_setting("loan_interest_rate", percent / 100)
    await message.answer(f"✅ Ставка по кредиту теперь {percent:.0f}% за срок.")


@dp.message(Command("setwarnthreshold"))
async def cmd_setwarnthreshold(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) < 1:
        await message.answer("Использование: <code>/setwarnthreshold [число]</code> — сколько варнов "
                              "до автосанкции.")
        return
    db.set_setting("warn_threshold", int(parts[1]))
    await message.answer(f"✅ Лимит предупреждений теперь {parts[1]}.")


@dp.message(Command("setwarnaction"))
async def cmd_setwarnaction(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or parts[1].lower() not in ("mute", "kick", "ban"):
        await message.answer("Использование: <code>/setwarnaction [mute/kick/ban]</code> — что происходит "
                              "при достижении лимита предупреждений.")
        return
    db.set_setting("warn_action", parts[1].lower())
    await message.answer(f"✅ Санкция за лимит предупреждений теперь: {parts[1].lower()}.")


@dp.message(Command("setmutetime"))
async def cmd_setmutetime(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: <code>/setmutetime [время]</code> — мут по умолчанию, "
                              "если в /mute не указано время явно. Пример: <code>/setmutetime 30m</code>")
        return
    seconds = parse_duration(parts[1])
    if seconds is None or seconds <= 0:
        await message.answer("Не понял время. Примеры: 30m, 1h.")
        return
    db.set_setting("mute_default_minutes", seconds // 60)
    await message.answer(f"✅ Мут по умолчанию теперь {format_duration(seconds)}.")


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
    await message.answer(f"✅ Баланс всех игроков сброшен до {fmt(start)} {cur()}.")


# ---------------- Group moderation (real Telegram mute/ban/warn, admin-only) ----------------

MUTE_PERMISSIONS_OFF = ChatPermissions(
    can_send_messages=False, can_send_other_messages=False,
    can_add_web_page_previews=False, can_send_polls=False,
)
MUTE_PERMISSIONS_ON = ChatPermissions(
    can_send_messages=True, can_send_other_messages=True,
    can_add_web_page_previews=True, can_send_polls=True,
)


async def _get_mod_target(message: Message):
    """Resolves the target user from a reply, blocking action on bots/admins/owner. Returns (user, error)."""
    if message.chat.type not in ("group", "supergroup"):
        return None, "Эта команда работает только в групповых чатах."
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return None, "Ответь (reply) на сообщение нужного пользователя."
    target = message.reply_to_message.from_user
    if target.is_bot:
        return None, "Нельзя применить к боту."
    if is_admin(target.id):
        return None, "Нельзя применить к админу или владельцу."
    if await is_telegram_group_admin(message.chat.id, target.id):
        return None, "Нельзя применить к админу этой группы."
    return target, None


def _parse_mod_args(text_after: str, default_seconds: int) -> tuple[int | None, str]:
    """Parses '[время] [причина]' or just '[причина]'. Returns (duration_seconds, reason);
    duration is None for permanent."""
    tokens = text_after.split(maxsplit=1)
    if not tokens:
        return default_seconds, "не указана"
    first = tokens[0].lower()
    rest = tokens[1] if len(tokens) > 1 else "не указана"
    if first in ("perm", "permanent", "forever", "навсегда", "форевер"):
        return None, rest
    duration = parse_duration(tokens[0])
    if duration is not None:
        return duration, rest
    return default_seconds, text_after


@dp.message(Command("mute"))
async def cmd_mute(message: Message):
    if not await can_moderate(message):
        return
    target, err = await _get_mod_target(message)
    if err:
        await message.answer(err)
        return
    text_after = message.text.partition(" ")[2].strip()
    default_seconds = int(db.get_setting("mute_default_minutes", 60)) * 60
    duration, reason = _parse_mod_args(text_after, default_seconds)
    try:
        if duration is None:
            await bot.restrict_chat_member(message.chat.id, target.id, permissions=MUTE_PERMISSIONS_OFF)
            duration_text = "навсегда"
        else:
            until = datetime.now(timezone.utc) + timedelta(seconds=duration)
            await bot.restrict_chat_member(message.chat.id, target.id, permissions=MUTE_PERMISSIONS_OFF,
                                            until_date=until)
            duration_text = format_duration(duration)
    except Exception as e:
        await message.answer(f"❌ Не получилось замутить — проверь, что бот админ в этой группе с правом "
                              f"ограничивать участников.\n({e})")
        return
    await message.answer(f"🔇 {target.first_name} замучен на {duration_text}.\nПричина: {reason}")


@dp.message(Command("unmute"))
async def cmd_unmute(message: Message):
    if not await can_moderate(message):
        return
    target, err = await _get_mod_target(message)
    if err:
        await message.answer(err)
        return
    try:
        await bot.restrict_chat_member(message.chat.id, target.id, permissions=MUTE_PERMISSIONS_ON)
    except Exception as e:
        await message.answer(f"❌ Не получилось размутить.\n({e})")
        return
    await message.answer(f"🔊 {target.first_name} размучен.")


@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if not await can_moderate(message):
        return
    target, err = await _get_mod_target(message)
    if err:
        await message.answer(err)
        return
    text_after = message.text.partition(" ")[2].strip()
    duration, reason = _parse_mod_args(text_after, 0)  # 0 default means "no duration" -> permanent below
    try:
        if not text_after or duration == 0:
            await bot.ban_chat_member(message.chat.id, target.id)
            duration_text = "навсегда"
        elif duration is None:
            await bot.ban_chat_member(message.chat.id, target.id)
            duration_text = "навсегда"
        else:
            until = datetime.now(timezone.utc) + timedelta(seconds=duration)
            await bot.ban_chat_member(message.chat.id, target.id, until_date=until)
            duration_text = format_duration(duration)
    except Exception as e:
        await message.answer(f"❌ Не получилось забанить — проверь, что бот админ в этой группе с правом "
                              f"банить участников.\n({e})")
        return
    await message.answer(f"🚫 {target.first_name} забанен ({duration_text}).\nПричина: {reason}")


@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if not await can_moderate(message):
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Эта команда работает только в групповых чатах.")
        return
    parts = message.text.split()
    target_id = None
    if len(parts) == 2 and parts[1].isdigit():
        target_id = int(parts[1])
    elif message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        await message.answer("Использование: <code>/unban [user_id]</code> или ответом на сообщение игрока.")
        return
    try:
        await bot.unban_chat_member(message.chat.id, target_id, only_if_banned=True)
    except Exception as e:
        await message.answer(f"❌ Не получилось разбанить.\n({e})")
        return
    await message.answer(f"✅ Игрок <code>{target_id}</code> разбанен.")


@dp.message(Command("kick"))
async def cmd_kick(message: Message):
    if not await can_moderate(message):
        return
    target, err = await _get_mod_target(message)
    if err:
        await message.answer(err)
        return
    text_after = message.text.partition(" ")[2].strip() or "не указана"
    try:
        await bot.ban_chat_member(message.chat.id, target.id)
        await bot.unban_chat_member(message.chat.id, target.id, only_if_banned=True)
    except Exception as e:
        await message.answer(f"❌ Не получилось кикнуть.\n({e})")
        return
    await message.answer(f"👢 {target.first_name} кикнут (может зайти обратно по ссылке).\nПричина: {text_after}")


@dp.message(Command("warn"))
async def cmd_warn(message: Message):
    if not await can_moderate(message):
        return
    target, err = await _get_mod_target(message)
    if err:
        await message.answer(err)
        return
    reason = message.text.partition(" ")[2].strip() or "не указана"
    now = datetime.now(timezone.utc).isoformat()
    db.add_warning(message.chat.id, target.id, reason, message.from_user.id, now)
    count = db.count_warnings(message.chat.id, target.id)
    threshold = int(db.get_setting("warn_threshold", 3))
    text = f"⚠️ {target.first_name} получил предупреждение ({count}/{threshold})\nПричина: {reason}"

    if count >= threshold:
        db.clear_warnings(message.chat.id, target.id)
        action = db.get_setting("warn_action", "mute")
        try:
            if action == "ban":
                await bot.ban_chat_member(message.chat.id, target.id)
                text += f"\n\n🚫 Лимит предупреждений достигнут — {target.first_name} забанен."
            elif action == "kick":
                await bot.ban_chat_member(message.chat.id, target.id)
                await bot.unban_chat_member(message.chat.id, target.id, only_if_banned=True)
                text += f"\n\n👢 Лимит предупреждений достигнут — {target.first_name} кикнут."
            else:
                mute_minutes = int(db.get_setting("warn_mute_minutes", 60))
                until = datetime.now(timezone.utc) + timedelta(minutes=mute_minutes)
                await bot.restrict_chat_member(message.chat.id, target.id, permissions=MUTE_PERMISSIONS_OFF,
                                                until_date=until)
                text += f"\n\n🔇 Лимит предупреждений достигнут — {target.first_name} замучен на {mute_minutes} мин."
        except Exception as e:
            text += f"\n\n❌ Не получилось применить санкцию автоматически.\n({e})"

    await message.answer(text)


@dp.message(Command("unwarn"))
async def cmd_unwarn(message: Message):
    if not await can_moderate(message):
        return
    target, err = await _get_mod_target(message)
    if err:
        await message.answer(err)
        return
    if not db.remove_last_warning(message.chat.id, target.id):
        await message.answer(f"У {target.first_name} нет предупреждений.")
        return
    count = db.count_warnings(message.chat.id, target.id)
    await message.answer(f"✅ Предупреждение снято у {target.first_name}. Осталось: {count}")


@dp.message(Command("warns"))
async def cmd_warns(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Эта команда работает только в групповых чатах.")
        return
    if message.reply_to_message and message.reply_to_message.from_user:
        if not await can_moderate(message):
            await message.answer("Смотреть чужие предупреждения могут только админы группы.")
            return
        target = message.reply_to_message.from_user
    else:
        target = message.from_user
    rows = db.get_warnings(message.chat.id, target.id)
    if not rows:
        await message.answer(f"У {target.first_name} нет предупреждений.")
        return
    text = f"⚠️ <b>Предупреждения {target.first_name}:</b> {len(rows)}\n\n"
    for i, r in enumerate(rows, 1):
        text += f"{i}. {r['reason']}\n"
    await message.answer(text)


# ---------------- Owner: create/remove currency (печать бабла — только владелец) ----------------

@dp.message(Command("give"))
async def cmd_give(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/give [user_id] [сумма]</code>\n"
                              "Сумма может быть отрицательной, чтобы забрать средства.")
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
    await message.answer(f"✅ Игроку <code>{target_id}</code> начислено {fmt(amount)}. Новый баланс: {fmt(new_balance)}")
    try:
        sign = "➕" if amount >= 0 else "➖"
        await bot.send_message(target_id, f"{sign} Владелец изменил твой баланс на {fmt(amount)}.\n"
                                           f"Текущий баланс: <b>{fmt(new_balance)}</b> {cur()}")
    except Exception:
        pass  # user may have blocked the bot


@dp.message(Command("take"))
async def cmd_take(message: Message):
    if not is_owner(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/take [user_id] [сумма]</code> — списать средства у игрока "
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
    await message.answer(f"✅ У игрока <code>{target_id}</code> списано {fmt(removed)} {cur()}. Баланс: {fmt(new_balance)}")
    try:
        await bot.send_message(target_id, f"➖ Владелец списал <b>{fmt(removed)}</b> {cur()}.\n"
                                           f"Текущий баланс: <b>{fmt(new_balance)}</b> {cur()}")
    except Exception:
        pass


@dp.message(Command("setbalance"))
async def cmd_setbalance(message: Message):
    if not is_owner(message.from_user.id):
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
    await message.answer(f"✅ Баланс игрока <code>{target_id}</code> выставлен в {fmt(amount)}.")


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
        await bot.send_message(target_id, "🎉 Тебя назначили админом бота! Доступны /mute, /warn, /ban, "
                                           "/broadcast и другие — загляни в меню команд (кнопка /).")
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
        owner_balance_text = fmt(db.get_balance(OWNER_ID)) if db.user_exists(OWNER_ID) else "—"
        text += f"{OWNER_ID} (баланс: {owner_balance_text} {cur()})\n\n"
    else:
        text += "не задан\n\n"

    text += "🛡 <b>Админы</b>\n"
    if ids:
        for i in ids:
            balance_text = fmt(db.get_balance(i)) if db.user_exists(i) else "—"
            text += f"— {i} (баланс: {balance_text} {cur()})\n"
    else:
        text += "пока нет.\n"

    text += "\nℹ️ Балансы владельца и админов ведутся отдельно от рейтинга игроков — " \
            "они не отображаются в /tops и /grouptop."
    await message.answer(text)


async def crypto_price_loop():
    while True:
        await asyncio.sleep(CRYPTO_UPDATE_INTERVAL_SECONDS)
        try:
            for row in db.get_all_crypto_prices():
                symbol = row["symbol"]
                price = row["price"]
                volatility = CRYPTO_VOLATILITY.get(symbol, 0.03)
                change = random.uniform(-volatility, volatility)
                new_price = max(price * (1 + change), price * 0.01)  # never crash to literal zero
                db.update_crypto_price(symbol, new_price, price)
        except Exception:
            logging.exception("Crypto price update failed")


async def main():
    db.init_db()
    await setup_default_menus()
    asyncio.create_task(crypto_price_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
