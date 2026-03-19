import asyncio
import sqlite3
import random
import itertools
from datetime import datetime
import os

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(TOKEN)
dp = Dispatcher()

MAX_PLAYERS = 15

registration = {}
self_rating_process = {}
rating_process = {}
votes = {}
game_message = None
game_launches = []

# ---------- DATABASE ----------

conn = sqlite3.connect("players.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS players(
    tg_id INTEGER PRIMARY KEY,
    name TEXT,
    self_stamina INTEGER,
    self_experience INTEGER,
    self_speed INTEGER,
    self_technique INTEGER,
    final_rating REAL,
    status TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS ratings(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER,
    rater_id INTEGER,
    stamina INTEGER,
    experience INTEGER,
    speed INTEGER,
    technique INTEGER
)
""")

conn.commit()

# ---------- DB FUNCTIONS ----------

def save_player(tg_id, name, data):
    cursor.execute("""
    INSERT OR REPLACE INTO players(
        tg_id, name,
        self_stamina, self_experience, self_speed, self_technique,
        final_rating, status
    )
    VALUES(?,?,?,?,?,?,?,?)
    """, (
        tg_id,
        name,
        data["stamina"],
        data["experience"],
        data["speed"],
        data["technique"],
        None,
        "не оценен"
    ))
    conn.commit()


def save_rating(player_id, rater_id, data):
    cursor.execute("""
    INSERT INTO ratings(player_id, rater_id, stamina, experience, speed, technique)
    VALUES(?,?,?,?,?,?)
    """, (
        player_id,
        rater_id,
        data["stamina"],
        data["experience"],
        data["speed"],
        data["technique"]
    ))
    conn.commit()


def has_already_rated(player_id, rater_id):
    cursor.execute("""
    SELECT 1 FROM ratings WHERE player_id=? AND rater_id=?
    """, (player_id, rater_id))
    return cursor.fetchone()


def get_players_for_rating(rater_id):
    cursor.execute("SELECT tg_id, name FROM players WHERE tg_id != ?", (rater_id,))
    return cursor.fetchall()


def get_player(tg_id):
    cursor.execute("SELECT * FROM players WHERE tg_id=?", (tg_id,))
    return cursor.fetchone()


def update_player_rating(player_id):
    cursor.execute("""
    SELECT stamina, experience, speed, technique
    FROM ratings WHERE player_id=?
    """, (player_id,))
    rows = cursor.fetchall()

    cursor.execute("""
    SELECT self_stamina, self_experience, self_speed, self_technique
    FROM players WHERE tg_id=?
    """, (player_id,))
    self_data = cursor.fetchone()

    if len(rows) < 5:
        return

    total = 0
    count = 0

    for r in rows:
        total += sum(r)
        count += 4

    if self_data:
        total += sum(self_data)
        count += 4

    avg = total / count

    cursor.execute("""
    UPDATE players SET final_rating=?, status='оценен'
    WHERE tg_id=?
    """, (avg, player_id))
    conn.commit()

# ---------- KEYBOARDS ----------

def rating_keyboard(player_id, criterion):
    buttons = []
    row = []

    for i in range(1, 11):
        row.append(
            InlineKeyboardButton(
                text=str(i),
                callback_data=f"rate_{player_id}_{criterion}_{i}"
            )
        )
        if len(row) == 5:
            buttons.append(row)
            row = []

    if row:
        buttons.append(row)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


register_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📝 Зарегистрироваться", callback_data="register")]]
)

rate_players_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📊 Оценить игроков", callback_data="rate_players")]]
)

# ---------- START ----------

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer("Нажми для регистрации 👇", reply_markup=register_keyboard)

# ---------- REGISTRATION ----------

@dp.callback_query(lambda c: c.data == "register")
async def register(call: types.CallbackQuery):
    registration[call.from_user.id] = True
    await call.message.answer("Введите имя")
    await call.answer()


@dp.message(lambda m: m.from_user.id in registration)
async def get_name(message: types.Message):
    user_id = message.from_user.id

    self_rating_process[user_id] = {
        "name": message.text
    }

    await message.answer("1️⃣ Выносливость", reply_markup=rating_keyboard(user_id, "self_stamina"))


@dp.callback_query(lambda c: "self_" in c.data)
async def self_rating(call: types.CallbackQuery):
    _, user_id, criterion, value = call.data.split("_")

    user_id = int(user_id)
    value = int(value)

    data = self_rating_process[user_id]
    data[criterion.replace("self_", "")] = value

    if criterion == "self_stamina":
        await call.message.edit_text("2️⃣ Опыт", reply_markup=rating_keyboard(user_id, "self_experience"))

    elif criterion == "self_experience":
        await call.message.edit_text("3️⃣ Скорость", reply_markup=rating_keyboard(user_id, "self_speed"))

    elif criterion == "self_speed":
        await call.message.edit_text("4️⃣ Техника", reply_markup=rating_keyboard(user_id, "self_technique"))

    else:
        save_player(user_id, data["name"], data)

        del self_rating_process[user_id]
        del registration[user_id]

        await call.message.edit_text("✅ Регистрация завершена", reply_markup=rate_players_keyboard)

    await call.answer()

# ---------- RATING ----------

@dp.callback_query(lambda c: c.data == "rate_players")
async def start_rating(call: types.CallbackQuery):
    players = get_players_for_rating(call.from_user.id)

    if not players:
        await call.answer("Нет игроков", show_alert=True)
        return

    rating_process[call.from_user.id] = {
        "players": players,
        "index": 0,
        "data": {}
    }

    await send_next_player(call.message, call.from_user.id)
    await call.answer()


async def send_next_player(message, user_id):
    process = rating_process[user_id]

    if process["index"] >= len(process["players"]):
        await message.answer("✅ Все игроки оценены")
        return

    player_id, name = process["players"][process["index"]]
    process["data"] = {"player_id": player_id}

    await message.answer(
        f"Оцени: {name}\n1️⃣ Выносливость",
        reply_markup=rating_keyboard(player_id, "stamina")
    )


@dp.callback_query(lambda c: c.data.startswith("rate_") and "self" not in c.data)
async def rate_player(call: types.CallbackQuery):
    _, player_id, criterion, value = call.data.split("_")

    player_id = int(player_id)
    value = int(value)
    user_id = call.from_user.id

    process = rating_process[user_id]
    data = process["data"]

    data[criterion] = value

    if criterion == "stamina":
        await call.message.edit_text("2️⃣ Опыт", reply_markup=rating_keyboard(player_id, "experience"))

    elif criterion == "experience":
        await call.message.edit_text("3️⃣ Скорость", reply_markup=rating_keyboard(player_id, "speed"))

    elif criterion == "speed":
        await call.message.edit_text("4️⃣ Техника", reply_markup=rating_keyboard(player_id, "technique"))

    elif criterion == "technique":
        save_rating(player_id, user_id, data)
        update_player_rating(player_id)

        process["index"] += 1

        await call.message.edit_text("✅ Сохранено")
        await send_next_player(call.message, user_id)

    await call.answer()

# ---------- RUN ----------

async def main():
    await dp.start_polling(bot)

asyncio.run(main())
