import asyncio
import logging
import os
import random
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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


dp.message.middleware(GroupTrackerMiddleware())


def parse_bet(arg: str, balance: int) -> int | None:
    try:
        amount = int(arg)
    except ValueError:
        return None
    if amount <= 0 or amount > balance:
        return None
    return amount


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
    await message.answer(
        f"🎰 <b>Добро пожаловать в виртуальное казино!</b>\n\n"
        f"Это чисто игровой бот — валюта не настоящая, ставки нельзя вывести.\n"
        f"Стартовый баланс: <b>{user['balance']}</b> голды 🪙\n\n"
        f"<b>Команды:</b>\n"
        f"/balance — баланс\n"
        f"/coinflip [ставка] [orel/reshka] — орёл/решка\n"
        f"/dice [ставка] [more/less] — кости (больше/меньше 3.5)\n"
        f"/slots [ставка] — слот-машина\n"
        f"/roulette [ставка] [red/black/green/число] — рулетка\n"
        f"/rps [ставка] [rock/paper/scissors] — камень-ножницы-бумага против бота\n"
        f"/wheel [ставка] — колесо фортуны\n"
        f"/mines [ставка] [мин 1-24] — мины, забирай выигрыш пока не подорвался\n"
        f"/blackjack [ставка] — блэкджек (21) против дилера\n"
        f"/hl [ставка] [higher/lower] — угадай, больше или меньше будет карта\n"
        f"/crash [ставка] — крэш-график (трейдинг), жми «Забрать» до краха\n"
        f"/daily — ежедневный бонус\n"
        f"/pay [user_id] [сумма] — перевести голду игроку (или ответом на его сообщение: /pay 100)\n"
        f"/top — таблица лидеров\n"
        f"/myid — узнать свой Telegram ID\n\n"
        f"<b>В группах:</b>\n"
        f"/duel [ставка] — вызвать соперника на дуэль (ответом на его сообщение)\n"
        f"/grouptop — топ игроков именно этого чата\n"
    )


@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    await message.answer(f"🪙 Твой баланс: <b>{user['balance']}</b> голды")


@dp.message(Command("myid"))
async def cmd_myid(message: Message):
    await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")


@dp.message(Command("daily"))
async def cmd_daily(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    uid = user["user_id"]
    last_bonus_str = db.get_last_bonus(uid)
    now = datetime.now(timezone.utc)

    if last_bonus_str:
        last_bonus = datetime.fromisoformat(last_bonus_str)
        elapsed = now - last_bonus
        remaining = timedelta(hours=DAILY_BONUS_COOLDOWN_HOURS) - elapsed
        if remaining.total_seconds() > 0:
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            minutes = rem // 60
            await message.answer(f"⏳ Бонус уже забирал. Приходи через <b>{hours}ч {minutes}м</b>.")
            return

    db.admin_add_balance(uid, DAILY_BONUS_AMOUNT)
    db.set_last_bonus(uid, now.isoformat())
    new_balance = db.get_balance(uid)
    await message.answer(
        f"🎁 Ежедневный бонус получен: <b>+{DAILY_BONUS_AMOUNT}</b> голды!\n"
        f"Баланс: <b>{new_balance}</b> 🪙\nПриходи через 24 часа за новым."
    )


@dp.message(Command("top"))
async def cmd_top(message: Message):
    rows = db.top_players(10)
    if not rows:
        await message.answer("Пока нет игроков.")
        return
    text = "🏆 <b>Топ игроков:</b>\n\n"
    for i, r in enumerate(rows, 1):
        name = r["username"] or "Аноним"
        text += f"{i}. {name} — {r['balance']} 🪙\n"
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
        await message.answer(f"🪙 Выпало: <b>{label}</b>\n✅ Ты выиграл <b>{amount}</b> голды!")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🪙 Выпало: <b>{label}</b>\n❌ Ты проиграл <b>{amount}</b> голды.")


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
        await message.answer(f"🎲 Выпало: <b>{roll}</b>\n✅ Ты выиграл <b>{amount}</b> голды!")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎲 Выпало: <b>{roll}</b>\n❌ Ты проиграл <b>{amount}</b> голды.")


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
        await message.answer(f"🎰 [ {reels_text} ]\n✅ Множитель x{multiplier}! Выигрыш: <b>{winnings}</b> голды")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎰 [ {reels_text} ]\n❌ Проигрыш <b>{amount}</b> голды.")


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
                              f"✅ Выигрыш x{multiplier}: <b>{winnings}</b> голды!")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎡 Выпало: <b>{number}</b> ({color_ru})\n"
                              f"❌ Проигрыш <b>{amount}</b> голды.")


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
        await message.answer(text + f"✅ Победа! Выигрыш: <b>{amount}</b> голды")
    elif result == "lose":
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(text + f"❌ Проигрыш <b>{amount}</b> голды.")
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
        await message.answer(f"🎡 Колесо остановилось на <b>x{multiplier}</b>!\nВыигрыш: <b>{winnings}</b> голды")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎡 Колесо остановилось на <b>x0</b>...\n❌ Проигрыш <b>{amount}</b> голды.")


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
            text=f"🏆 <b>Все безопасные клетки открыты!</b>\nВыигрыш x{state['multiplier']}: <b>{winnings}</b> голды 🪙",
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
        text=f"✅ <b>Забрал на x{state['multiplier']}!</b>\nВыигрыш: <b>{winnings}</b> голды 🪙",
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
        await message.answer(text + f"✅ Угадал! Выигрыш: <b>{amount}</b> голды")
    elif outcome == "lose":
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(text + f"❌ Не угадал. Проигрыш <b>{amount}</b> голды.")
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
        result = f"🏆 Блэкджек! Выигрыш x2.5: <b>{winnings}</b> голды"
    elif dealer_val > 21 or player_val > dealer_val:
        winnings = bet * 2
        db.change_balance(uid, winnings, won=True)
        result = f"✅ Победа! Выигрыш: <b>{winnings}</b> голды"
    elif player_val == dealer_val:
        db.change_balance(uid, bet)  # push, return the bet
        result = "🤝 Ничья, ставка возвращена."
    else:
        result = f"❌ Дилер сильнее. Проигрыш <b>{bet}</b> голды."

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
              f"Выигрыш: <b>{winnings}</b> голды 🪙"),
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
                              "Сумма может быть отрицательной, чтобы забрать голду.")
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
                                           f"Текущий баланс: <b>{new_balance}</b> 🪙")
    except Exception:
        pass  # user may have blocked the bot


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


@dp.message(Command("giveaway"))
async def cmd_giveaway(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: <code>/giveaway [приз] [секунд]</code>\n"
                              "Пример: <code>/giveaway 1000 60</code>")
        return
    try:
        prize = int(parts[1])
        duration = int(parts[2])
    except ValueError:
        await message.answer("Приз и время должны быть числами.")
        return

    chat_id = message.chat.id
    if chat_id in active_giveaways:
        await message.answer("В этом чате уже идёт розыгрыш!")
        return

    sent = await message.answer(
        f"🎉 <b>РОЗЫГРЫШ!</b>\nПриз: <b>{prize}</b> голды 🪙\n"
        f"Жми кнопку, чтобы участвовать!\nИтоги через {duration} сек.",
        reply_markup=giveaway_keyboard(),
    )
    active_giveaways[chat_id] = {
        "prize": prize,
        "participants": set(),
        "message_id": sent.message_id,
    }

    async def finish_giveaway():
        await asyncio.sleep(duration)
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
            f"🎉 <b>Розыгрыш завершён!</b>\nПобедитель: {winner_name}\nВыигрыш: <b>{state['prize']}</b> голды 🪙"
        )

    asyncio.create_task(finish_giveaway())


@dp.callback_query(F.data == "giveaway_join")
async def cb_giveaway_join(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    state = active_giveaways.get(chat_id)
    if state is None:
        await callback.answer("Розыгрыш уже завершён.", show_alert=True)
        return
    db.get_or_create_user(callback.from_user.id, callback.from_user.username or callback.from_user.first_name)
    if callback.from_user.id in state["participants"]:
        await callback.answer("Ты уже участвуешь!")
        return
    state["participants"].add(callback.from_user.id)
    await callback.answer("Участвуешь в розыгрыше! 🎉")


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
            await message.answer("Нельзя перевести голду боту.")
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
        await message.answer("Нельзя перевести голду самому себе 🙂")
        return

    try:
        amount = int(amount_str)
    except ValueError:
        await message.answer("Сумма должна быть числом.")
        return
    if amount < MIN_TRANSFER:
        await message.answer(f"Минимальная сумма перевода — {MIN_TRANSFER} голды.")
        return
    if amount > sender["balance"]:
        await message.answer("У тебя не хватает голды для этого перевода.")
        return

    db.change_balance(message.from_user.id, -amount)
    db.admin_add_balance(target_id, amount)  # credit without touching games_played stats

    await message.answer(f"✅ Переведено <b>{amount}</b> голды 🪙")
    try:
        sender_name = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
        await bot.send_message(target_id, f"💸 {sender_name} перевёл(а) тебе <b>{amount}</b> голды 🪙")
    except Exception:
        pass  # recipient may have blocked the bot


# ---------------- Group features: duels & group leaderboard ----------------

@dp.message(Command("grouptop"))
async def cmd_grouptop(message: Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Эта команда работает только в групповых чатах.")
        return
    rows = db.get_group_top(message.chat.id, 10)
    if not rows:
        await message.answer("В этом чате пока никто не играл.")
        return
    text = "🏆 <b>Топ игроков этого чата:</b>\n\n"
    for i, r in enumerate(rows, 1):
        name = r["username"] or "Аноним"
        text += f"{i}. {name} — {r['balance']} 🪙\n"
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
        f"{message.from_user.first_name} вызывает {opponent.first_name} на ставку <b>{amount}</b> голды!\n"
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
        await callback.message.edit_text("❌ У вызвавшего игрока больше не хватает голды. Дуэль отменена.")
        await callback.answer()
        return
    if target_balance < amount:
        await callback.answer(f"У тебя не хватает голды для ставки {amount}!", show_alert=True)
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
        f"⚔️ <b>Дуэль завершена!</b>\n🏆 Победитель: {winner_name} (+{amount * 2} 🪙)\n😢 Проигравший: {loser_name}"
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
    await message.answer(f"✅ <code>{target_id}</code> назначен админом.")
    try:
        await bot.send_message(target_id, "🎉 Тебя назначили админом бота! Доступны /give, /broadcast, /giveaway.")
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
    await message.answer(f"✅ <code>{target_id}</code> больше не админ.")


@dp.message(Command("admins"))
async def cmd_admins(message: Message):
    if not is_admin(message.from_user.id):
        return
    ids = db.list_admins()
    text = "👑 <b>Владелец:</b> " + (str(OWNER_ID) if OWNER_ID else "не задан") + "\n\n"
    if ids:
        text += "🛡 <b>Админы:</b>\n" + "\n".join(f"— {i}" for i in ids)
    else:
        text += "Админов (кроме владельца) пока нет."
    await message.answer(text)


async def main():
    db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
