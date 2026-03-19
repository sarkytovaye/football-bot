import asyncio
from operator import call
import sqlite3
import random
import itertools
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import os

TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(TOKEN)
dp = Dispatcher()

MAX_PLAYERS = 15

registration = {}
votes = {}
rating_process = {}
game_message = None
game_launches = []

# ---------- DATABASE ----------

conn = sqlite3.connect("players.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS players(
    tg_id INTEGER PRIMARY KEY,
    name TEXT,
    self_rating INTEGER,
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

def save_player(tg_id, name):
    cursor.execute("""
    INSERT OR REPLACE INTO players(tg_id, name, status)
    VALUES(?,?,?)
    """, (tg_id, name, "не оценен"))
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


def update_player_rating(player_id):
    cursor.execute("""
    SELECT stamina, experience, speed, technique
    FROM ratings
    WHERE player_id=?
    """, (player_id,))

    rows = cursor.fetchall()

    if len(rows) < 5:
        cursor.execute("""
        UPDATE players SET status='не оценен', final_rating=NULL
        WHERE tg_id=?
        """, (player_id,))
        conn.commit()
        return

    total = 0
    count = 0

    for r in rows:
        total += sum(r)
        count += 4

    avg = total / count

    cursor.execute("""
    UPDATE players
    SET final_rating=?, status='оценен'
    WHERE tg_id=?
    """, (avg, player_id))

    conn.commit()


def get_players_for_rating(rater_id):
    cursor.execute("""
    SELECT tg_id, name FROM players WHERE tg_id != ?
    """, (rater_id,))
    return cursor.fetchall()

def save_player(tg_id, name, self_rating):

    cursor.execute(
        "INSERT OR REPLACE INTO players VALUES(?,?,?,?,?)", (tg_id, name, self_rating, 0.0, "pending")
    )

    conn.commit()


def get_player(tg_id):

    cursor.execute("SELECT * FROM players WHERE tg_id=?", (tg_id,))

    return cursor.fetchone()


# ---------- KEYBOARDS ----------

rating_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="1", callback_data="rate_1"),
            InlineKeyboardButton(text="2", callback_data="rate_2"),
            InlineKeyboardButton(text="3", callback_data="rate_3"),
            InlineKeyboardButton(text="4", callback_data="rate_4"),
            InlineKeyboardButton(text="5", callback_data="rate_5"),
        ]
    ]
)

play_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="⚽ Играю", callback_data="play_yes"),
            InlineKeyboardButton(text="❌ Не играю", callback_data="play_no"),
        ]
    ]
)

shuffle_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Перемешать команды", callback_data="shuffle")]
    ]
)

register_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📝 Зарегистрироваться", callback_data="register_btn")]
    ]
)

rate_players_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📊 Оценить игроков", callback_data="rate_players")]
    ]
)
def rating_keyboard_10(player_id, criterion):
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
# ---------- REGISTRATION ----------

@dp.message(Command("start"))
async def start(message: types.Message):
    await message.answer(
        "Добро пожаловать!\n\nНажмите кнопку ниже для регистрации 👇",
        reply_markup=register_keyboard
    )
    
@dp.callback_query(lambda c: c.data == "register_btn")
async def register_button(call: types.CallbackQuery):

    user_id = call.from_user.id

    player = get_player(user_id)

    if player:
        await call.answer("Вы уже зарегистрированы ✅", show_alert=True)
        return

    registration[user_id] = {}

    await call.message.answer("Введите ваше имя")
    await call.answer()


@dp.message(
    lambda message: message.from_user.id in registration
    and not message.text.startswith("/")
)
async def get_name(message: types.Message):

    user_id = message.from_user.id
    data = registration[user_id]

    if "name" not in data:
        data["name"] = message.text

        await message.answer("🏃 Оцените Бег", reply_markup=rating_keyboard)

@dp.callback_query(lambda c: c.data.startswith("rate_") and len(c.data.split("_")) == 4)
async def process_rating(call: types.CallbackQuery):

    _, player_id, criterion, value = call.data.split("_")

    player_id = int(player_id)
    value = int(value)
    rater_id = call.from_user.id

    key = (rater_id, player_id)

    if key not in rating_process:
        rating_process[key] = {}

    rating_process[key][criterion] = value
    data = rating_process[key]

    if criterion == "stamina":
        await call.message.edit_text(
            "2️⃣ Опыт",
            reply_markup=rating_keyboard_10(player_id, "experience")
        )

    elif criterion == "experience":
        await call.message.edit_text(
            "3️⃣ Скорость",
            reply_markup=rating_keyboard_10(player_id, "speed")
        )

    elif criterion == "speed":
        await call.message.edit_text(
            "4️⃣ Техника",
            reply_markup=rating_keyboard_10(player_id, "technique")
        )

    elif criterion == "technique":

        save_rating(player_id, rater_id, data)
        update_player_rating(player_id)

        del rating_process[key]

        await call.message.edit_text("✅ Оценка сохранена")

@dp.callback_query(lambda c: c.data.startswith("rate_") and len(c.data.split("_")) == 2)
async def rate(call: types.CallbackQuery):

    user_id = call.from_user.id

    if user_id not in registration:
        return

    value = int(call.data.split("_")[1])

    data = registration[user_id]

    if "run" not in data:

        data["run"] = value

        await call.message.edit_text("⚽ Оцените Удар", reply_markup=rating_keyboard)

        return

    if "shot" not in data:

        data["shot"] = value

        await call.message.edit_text("🎯 Оцените Пас", reply_markup=rating_keyboard)

        return

    if "pass" not in data:

        data["pass"] = value

        rating = data["run"] + data["shot"] + data["pass"]

        save_player(user_id, data["name"])

        del registration[user_id]

        await call.message.edit_text(
            "✅ Регистрация завершена\n\nТеперь оцените других игроков 👇",
        reply_markup=rate_players_keyboard
        )


# ---------- CREATE GAME ----------


@dp.message(Command("game"))
async def game(message: types.Message):

    global game_message

    now = datetime.now()

    game_launches[:] = [t for t in game_launches if (now - t).days < 3]

    if len(game_launches) >= 1:

        await message.answer("⚠ Игру можно запускать только 1 раз в 3 дня")
        return

    game_launches.append(now)

    votes.clear()

    game_message = await message.answer(
        "⚽ Игра\n\nИграют (0/15)", reply_markup=play_keyboard
    )


# ---------- UPDATE LIST ----------


async def update_list():

    playing = [u for u, v in votes.items() if v == "yes"]

    total_rating = 0

    text = f"⚽ Игра\n\nИграют ({len(playing)}/{MAX_PLAYERS})\n\n"

    for i, user in enumerate(playing, 1):

        player = get_player(user)

        if player:

            rating = player[2]

            total_rating += rating

            text += f"{i}. {player[1]} ({rating})\n"

    text += f"\n📊 Общий рейтинг группы: {total_rating}\n"

    if len(playing) >= MAX_PLAYERS:

        text += "\n⛔ Набор игроков завершён"

        await game_message.edit_text(text)

    else:

        await game_message.edit_text(text, reply_markup=play_keyboard)


# ---------- BUTTON YES ----------


@dp.callback_query(lambda c: c.data == "play_yes")
async def play_yes(call: types.CallbackQuery):

    player = get_player(call.from_user.id)

    if not player:
        await call.message.answer(
        "Сначала зарегистрируйтесь 👇",
        reply_markup=register_keyboard
    )
        await call.answer()
        return

    votes[call.from_user.id] = "yes"

    await update_list()


# ---------- BUTTON NO ----------


@dp.callback_query(lambda c: c.data == "play_no")
async def play_no(call: types.CallbackQuery):

    votes[call.from_user.id] = "no"

    await update_list()

@dp.callback_query(lambda c: c.data == "rate_players")
async def start_rating(call: types.CallbackQuery):

    players = get_players_for_rating(call.from_user.id)

    if not players:
        await call.answer("Нет игроков для оценки", show_alert=True)
        return

    player_id, name = players[0]

    if has_already_rated(player_id, call.from_user.id):
        await call.answer("Вы уже оценили этого игрока", show_alert=True)
        return

    await call.message.answer(
        f"Оцените игрока: {name}\n\n1️⃣ Выносливость",
        reply_markup=rating_keyboard_10(player_id, "stamina")
    )
# ---------- PERFECT BALANCE ----------


def perfect_balance(players):

    best_diff = 999
    best_team1 = None
    best_team2 = None

    for combo in itertools.combinations(players, 5):

        team1 = list(combo)
        team2 = [p for p in players if p not in team1]

        r1 = sum(p["rating"] for p in team1)
        r2 = sum(p["rating"] for p in team2)

        diff = abs(r1 - r2)

        if diff < best_diff:

            best_diff = diff
            best_team1 = team1
            best_team2 = team2

    return best_team1, best_team2, diff


# ---------- CREATE TEAMS ----------


@dp.message(Command("teams"))
async def teams(message: types.Message):

    players = []

    for u, v in votes.items():

        if v == "yes":

            player = get_player(u)

            if player:

                players.append({"name": player[1], "rating": player[2]})

    if len(players) < 10:

        await message.answer("❗ Нужно минимум 10 игроков")
        return

    text = "⚽ Команды\n\n"

    if len(players) >= 15:

        teams = balance_teams(players[:15], 3, 5)

        for i, team in enumerate(teams, 1):

            total = team_total(team)
            avg = team_average(team)

            text += f"🔹 Команда {i} ({total} | ср. {avg})\n"

            for p in team:
                text += f"{p['name']} ({p['rating']})\n"

            text += "\n"

    else:

        main_players = players[:10]
        bench = players[10:]

        teams = balance_teams(main_players, 2, 5)

        text += "🔵 Команда\n"

        for p in teams[0]:
            text += p["name"] + "\n"

        text += "\n🔴 Команда\n"

        for p in teams[1]:
            text += p["name"] + "\n"

        if bench:

            text += "\n🪑 Запасные\n"

            for p in bench:
                text += p["name"] + "\n"

    await message.answer(text)


async def create_teams_auto(chat_id):

    players = []

    for u, v in votes.items():

        if v == "yes":

            player = get_player(u)

            if player:

                players.append({"name": player[1], "rating": player[2]})

    players = players[:15]

    teams = balance_teams(players, 3, 5)

    text = "⚽ Команды\n\n"

    for i, team in enumerate(teams, 1):

        text += f"🔹 Команда {i}\n"

        for p in team:
            text += p["name"] + "\n"

        text += "\n"

    await bot.send_message(chat_id, text)


def team_total(team):
    return sum(p["rating"] for p in team)


def team_average(team):

    if not team:
        return 0

    total = team_total(team)

    return round(total / len(team), 2)


# ---------- SHUFFLE ----------


@dp.callback_query(lambda c: c.data == "shuffle")
async def shuffle(call: types.CallbackQuery):

    member = await bot.get_chat_member(call.message.chat.id, call.from_user.id)

    if member.status not in ["administrator", "creator"]:

        await call.answer(
            "Только администратор может перемешивать команды", show_alert=True
        )
        return

    players = []

    for u, v in votes.items():

        if v == "yes":

            player = get_player(u)

            if player:

                players.append({"name": player[1], "rating": player[2]})

    random.shuffle(players)

    main_players = players[:10]

    team1, team2, diff = perfect_balance(main_players)

    text = "🔄 Новые команды\n\n"

    text += "🔵 Команда\n"

    for p in team1:
        text += p["name"] + "\n"

    text += "\n🔴 Команда\n"

    for p in team2:
        text += p["name"] + "\n"

    text += f"\nРазница рейтинга: {diff}\n"

    await call.message.edit_text(text, reply_markup=shuffle_keyboard)


# ---------- START BOT ----------


async def main():
    await dp.start_polling(bot)


asyncio.run(main())
