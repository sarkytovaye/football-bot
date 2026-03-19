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
self_rating_process = {}
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


def update_player_rating(player_id):

    cursor.execute("""
    SELECT stamina, experience, speed, technique
    FROM ratings
    WHERE player_id=?
    """, (player_id,))

    rows = cursor.fetchall()

    cursor.execute("""
    SELECT self_stamina, self_experience, self_speed, self_technique
    FROM players WHERE tg_id=?
    """, (player_id,))

    self_data = cursor.fetchone()

    total = 0
    count = 0

    # оценки других
    for r in rows:
        total += sum(r)
        count += 4

    # добавляем самооценку
    if self_data:
        total += sum(self_data)
        count += 4

    if len(rows) < 5:
        cursor.execute("""
        UPDATE players SET status='не оценен', final_rating=NULL
        WHERE tg_id=?
        """, (player_id,))
        conn.commit()
        return

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

def get_player(tg_id):

    cursor.execute("SELECT * FROM players WHERE tg_id=?", (tg_id,))

    return cursor.fetchone()

def get_player_rating(player_id):
    cursor.execute("""
    SELECT final_rating FROM players WHERE tg_id=?
    """, (player_id,))
    
    result = cursor.fetchone()
    
    if result and result[0]:
        return round(result[0], 2)
    
    return 0

def get_top_players(limit=10):
    cursor.execute("""
    SELECT name, final_rating 
    FROM players 
    WHERE final_rating IS NOT NULL
    ORDER BY final_rating DESC
    LIMIT ?
    """, (limit,))
    
    return cursor.fetchall()
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

    self_rating_process[user_id] = {
        "name": message.text
    }

    await message.answer(
        "Оцените себя\n\n1️⃣ Выносливость 1 - нет, 10 - может бегать без остановки 1 час",
        reply_markup=rating_keyboard_10(user_id, "self_stamina")
    )

@dp.callback_query(lambda c: c.data.startswith("rate_") and "self" in c.data)
async def self_rating(call: types.CallbackQuery):

    parts = call.data.split("_")
    
    user_id = int(parts[1])
    
    value = int(parts[-1])
    
    criterion = "_".join(parts[2:-1])  # собираем self_stamina
    
    data = self_rating_process[user_id]
    
    data[criterion.replace("self_", "")] = value

    if criterion == "self_stamina":
        await call.message.edit_text(
            "2️⃣ Опыт 1 - нет, 10 - играет больше 10 лет",
            reply_markup=rating_keyboard_10(user_id, "self_experience")
        )

    elif criterion == "self_experience":
        await call.message.edit_text(
            "3️⃣ Скорость 1-нет, 10 - тренированная скоростная выносливость",
            reply_markup=rating_keyboard_10(user_id, "self_speed")
        )

    elif criterion == "self_speed":
        await call.message.edit_text(
            "4️⃣ Техника 1-нет, 10 - професиональная техническая подготовка",
            reply_markup=rating_keyboard_10(user_id, "self_technique")
        )

    elif criterion == "self_technique":

        save_player(user_id, data["name"], data)

        del self_rating_process[user_id]
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

            rating = player[6] or 0

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
    if not game_message:
        return


# ---------- BUTTON NO ----------


@dp.callback_query(lambda c: c.data == "play_no")
async def play_no(call: types.CallbackQuery):

    votes[call.from_user.id] = "no"

    await update_list()
    if not game_message:
        return

@dp.callback_query(lambda c: c.data == "rate_players")
async def start_rating(call: types.CallbackQuery):

    players = get_players_for_rating(call.from_user.id)

    if not players:
        await call.answer("Нет игроков для оценки", show_alert=True)
        return

    rating_process[call.from_user.id] = {
        "players": players,
        "current_index": 0,
        "data": {}
    }

    await send_next_player(call.message, call.from_user.id)
    async def send_next_player(message, user_id):

        process = rating_process[user_id]
    players = process["players"]
    index = process["current_index"]

    if index >= len(players):

        del rating_process[user_id]

    top_players = get_top_players()

    text = "🏆 Топ игроков:\n\n"

    for i, (name, rating) in enumerate(top_players, 1):
        text += f"{i}. {name} — {round(rating, 2)}\n"

    await message.answer("✅ Вы оценили всех игроков!\n")
    await message.answer(text)

    return

    player_id, name = players[index]

    if has_already_rated(player_id, user_id):
        process["current_index"] += 1
        await send_next_player(message, user_id)
        return

    process["data"] = {
        "player_id": player_id,
        "name": name
    }

    await message.answer(
        f"👤 Игрок: {name}\n\n"
        f"1️⃣ Выносливость\n"
        f"Прогресс: {index+1}/{len(players)}",
        reply_markup=rating_keyboard_10(player_id, "stamina")
    )
    @dp.callback_query(lambda c: c.data.startswith("rate_") and "self" not in c.data)
    async def rate_player(call: types.CallbackQuery):

        parts = call.data.split("_")

    player_id = int(parts[1])
    criterion = parts[2]
    value = int(parts[3])

    user_id = call.from_user.id

    process = rating_process.get(user_id)

    if not process:
        await call.answer("Сессия устарела", show_alert=True)
        return

    data = process["data"]
    data[criterion] = value

    # --- Переходы между критериями ---
    if criterion == "stamina":
        await call.message.edit_text(
            f"👤 {data['name']}\n\n2️⃣ Опыт",
            reply_markup=rating_keyboard_10(player_id, "experience")
        )

    elif criterion == "experience":
        await call.message.edit_text(
            f"👤 {data['name']}\n\n3️⃣ Скорость",
            reply_markup=rating_keyboard_10(player_id, "speed")
        )

    elif criterion == "speed":
        await call.message.edit_text(
            f"👤 {data['name']}\n\n4️⃣ Техника",
            reply_markup=rating_keyboard_10(player_id, "technique")
        )

    elif criterion == "technique":

        save_rating(player_id, user_id, data)
        update_player_rating(player_id)

        avg = get_player_rating(player_id)

        process["current_index"] += 1

        await call.message.edit_text(
        f"✅ Оценка сохранена\n\n"
        f"📊 Новый рейтинг игрока: {avg}"
        )

        await send_next_player(call.message, user_id)
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

@dp.callback_query(lambda c: c.data.startswith("rate_") and "self" not in c.data)
async def rate_player(call: types.CallbackQuery):

    _, player_id, criterion, value = call.data.split("_")

    player_id = int(player_id)
    rater_id = call.from_user.id
    value = int(value)

    if has_already_rated(player_id, rater_id):
        await call.answer("Вы уже оценили этого игрока", show_alert=True)
        return

    if rater_id not in rating_process:
        rating_process[rater_id] = {}

    if player_id not in rating_process[rater_id]:
        rating_process[rater_id][player_id] = {}

    rating_process[rater_id][player_id][criterion] = value

    data = rating_process[rater_id][player_id]

    if len(data) == 4:
        save_rating(player_id, rater_id, data)
        update_player_rating(player_id)

        del rating_process[rater_id][player_id]

        await call.answer("Оценка сохранена ✅", show_alert=True)

@dp.message(Command("teams"))
async def teams(message: types.Message):

    players = []

    for u, v in votes.items():

        if v == "yes":

            player = get_player(u)

            if player:

                players.append({"name": player[1], "rating": player[6] or 0})

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

                players.append({"name": player[1], "rating": player[6] or 0})

    players = players[:15]

    teams = balance_teams(players, 3, 5)

    text = "⚽ Команды\n\n"

    for i, team in enumerate(teams, 1):

        text += f"🔹 Команда {i}\n"

        for p in team:
            text += p["name"] + "\n"

        text += "\n"

    await bot.send_message(chat_id, text)

def balance_teams(players, num_teams, team_size):
    random.shuffle(players)

    teams = []
    for i in range(num_teams):
        team = players[i*team_size:(i+1)*team_size]
        teams.append(team)

    return teams

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

                players.append({"name": player[1], "rating": player[6] or 0})

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
