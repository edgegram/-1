import asyncio
import logging
import os
import random
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F
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

DAILY_BONUS_AMOUNT = 500
DAILY_BONUS_COOLDOWN_HOURS = 24

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# active crash games: user_id -> dict(bet, multiplier, crash_point, cashed_out, task)
active_crash = {}

# active giveaway: chat_id -> dict(prize, participants:set, ends_task)
active_giveaways = {}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def parse_bet(arg: str, balance: int) -> int | None:
    try:
        amount = int(arg)
    except ValueError:
        return None
    if amount <= 0 or amount > balance:
        return None
    return amount


# ---------------- Basic commands ----------------

@dp.message(CommandStart())
async def cmd_start(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    await message.answer(
        f"🎰 <b>Добро пожаловать в виртуальное казино!</b>\n\n"
        f"Это чисто игровой бот — валюта не настоящая, ставки нельзя вывести.\n"
        f"Стартовый баланс: <b>{user['balance']}</b> фишек 🪙\n\n"
        f"<b>Команды:</b>\n"
        f"/balance — баланс\n"
        f"/coinflip [ставка] [orel/reshka] — орёл/решка\n"
        f"/dice [ставка] [more/less] — кости (больше/меньше 3.5)\n"
        f"/slots [ставка] — слот-машина\n"
        f"/crash [ставка] — крэш-график (трейдинг), жми «Забрать» до краха\n"
        f"/daily — ежедневный бонус\n"
        f"/top — таблица лидеров\n"
        f"/myid — узнать свой Telegram ID\n"
    )


@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    await message.answer(f"🪙 Твой баланс: <b>{user['balance']}</b> фишек")


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
        f"🎁 Ежедневный бонус получен: <b>+{DAILY_BONUS_AMOUNT}</b> фишек!\n"
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
        await message.answer(f"🪙 Выпало: <b>{label}</b>\n✅ Ты выиграл <b>{amount}</b> фишек!")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🪙 Выпало: <b>{label}</b>\n❌ Ты проиграл <b>{amount}</b> фишек.")


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
        await message.answer(f"🎲 Выпало: <b>{roll}</b>\n✅ Ты выиграл <b>{amount}</b> фишек!")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎲 Выпало: <b>{roll}</b>\n❌ Ты проиграл <b>{amount}</b> фишек.")


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
        await message.answer(f"🎰 [ {reels_text} ]\n✅ Множитель x{multiplier}! Выигрыш: <b>{winnings}</b> фишек")
    else:
        db.change_balance(user["user_id"], -amount, won=False)
        await message.answer(f"🎰 [ {reels_text} ]\n❌ Проигрыш <b>{amount}</b> фишек.")


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
              f"Выигрыш: <b>{winnings}</b> фишек 🪙"),
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
                              "Сумма может быть отрицательной, чтобы забрать фишки.")
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
        f"🎉 <b>РОЗЫГРЫШ!</b>\nПриз: <b>{prize}</b> фишек 🪙\n"
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
            f"🎉 <b>Розыгрыш завершён!</b>\nПобедитель: {winner_name}\nВыигрыш: <b>{state['prize']}</b> фишек 🪙"
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


async def main():
    db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
