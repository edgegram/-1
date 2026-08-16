import asyncio
import logging
import os
import random

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import database as db
import games

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# active crash games: user_id -> dict(bet, multiplier, crash_point, cashed_out, task)
active_crash = {}


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
        f"/top — таблица лидеров\n"
    )


@dp.message(Command("balance"))
async def cmd_balance(message: Message):
    user = db.get_or_create_user(message.from_user.id, message.from_user.username or message.from_user.first_name)
    await message.answer(f"🪙 Твой баланс: <b>{user['balance']}</b> фишек")


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


async def main():
    db.init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
