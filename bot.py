import asyncio
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
game_message = None
game_launches = []

# --- New: sessions for peer evaluation
eval_sessions = {}  # rater_id -> {target_id, step, data}

# ---------- DATABASE ----------

conn = sqlite3.connect("players.db")
cursor = conn.cursor()

# Ensure base players table exists (backwards compatible)
cursor.execute(
    '''
CREATE TABLE IF NOT EXISTS players(
    tg_id INTEGER PRIMARY KEY,
    name TEXT,
    rating INTEGER DEFAULT 0
)
'''
)
conn.commit()

# Migrate: add self-assessment columns and status if missing
def try_add_column(col_sql):
    try:
        cursor.execute(col_sql)
        conn.commit()
    except sqlite3.OperationalError:
        # column already exists
        pass

try_add_column('ALTER TABLE players ADD COLUMN self_endurance INTEGER')
try_add_column('ALTER TABLE players ADD COLUMN self_experience INTEGER')
try_add_column('ALTER TABLE players ADD COLUMN self_speed INTEGER')
try_add_column('ALTER TABLE players ADD COLUMN self_technique INTEGER')
try_add_column("ALTER TABLE players ADD COLUMN status TEXT DEFAULT 'не оценен'")

# Ratings from other users
cursor.execute(
    '''
CREATE TABLE IF NOT EXISTS ratings(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_tg_id INTEGER,
    rater_tg_id INTEGER,
    endurance INTEGER,
    experience INTEGER,
    speed INTEGER,
    technique INTEGER,
    created_at TEXT
)
'''
)
conn.commit()

# Save/update player with explicit columns
def save_player_full(tg_id, name, rating, self_e=None, self_ex=None, self_s=None, self_t=None, status='не оценен'):
    cursor.execute(
        '''INSERT OR REPLACE INTO players (tg_id, name, rating, self_endurance, self_experience, self_speed, self_technique, status)
           VALUES(?,?,?,?,?,?,?,?)''',
        (tg_id, name, rating, self_e, self_ex, self_s, self_t, status)
    )
    conn.commit()

# Get player row with columns in known order: tg_id, name, rating, self_endurance, self_experience, self_speed, self_technique, status
def get_player_full(tg_id):
    cursor.execute('SELECT tg_id, name, rating, self_endurance, self_experience, self_speed, self_technique, status FROM players WHERE tg_id=?', (tg_id,))
    return cursor.fetchone()

# Count distinct raters and compute average across all raters (all 4 criteria)
def compute_peer_stats(player_tg_id):
    cursor.execute('SELECT endurance, experience, speed, technique FROM ratings WHERE player_tg_id=?', (player_tg_id,))
    rows = cursor.fetchall()
    if not rows:
        return 0, 0.0
    raters = len(rows)
    total = sum(sum(r) for r in rows)
    avg = total / (raters * 4)
    return raters, avg

# ---------- KEYBOARDS ----------

# rating keyboard 1..10 (two rows of 5)
rating_keyboard_1_10 = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text=str(i), callback_data=f"val_{i}") for i in range(1,6)],
        [InlineKeyboardButton(text=str(i), callback_data=f"val_{i}") for i in range(6,11)],
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
        [InlineKeyboardButton(text="📝 Зарегистрироваться", callback_data="register_button")]
    ]
)

# Keyboard for listing players to rate is built dynamically

# ---------- REGISTRATION (name + self-assessment 4 criteria) ----------

@dp.callback_query(lambda c: c.data == "register_button") 
async def register_button(call: types.CallbackQuery): 
    user_id = call.from_user.id 
    player = get_player_full(user_id) 
    if player: await call.answer("Вы уже зарегистрированы ✅", show_alert=True) 
        return if user_id in registration: 
            await call.answer("Вы уже начали регистрацию. Введите имя.", show_alert=False) 
            return registration[user_id] = {"stage": "name"} try: 
                await bot.send_message(user_id, "Введите ваше имя") except Exception: 
                    await call.message.answer("Введите ваше имя") 
                    await call.answer()

@dp.message(lambda message: message.from_user.id in registration and not message.text.startswith("/"))
async def get_name(message: types.Message):
    user_id = message.from_user.id
    data = registration[user_id]
    if data.get("stage") == "name":
        data["name"] = message.text
        data["stage"] = "self_endurance"
        await message.answer("Оцените вашу Выносливость (1-10):", reply_markup=rating_keyboard_1_10)

# Handle value callbacks for registration and evaluation universally
@dp.callback_query(lambda c: c.data and c.data.startswith("val_"))
async def value_chosen(call: types.CallbackQuery):
    val = int(call.data.split("_")[1])
    user_id = call.from_user.id

    # If user is in registration flow
    if user_id in registration:
        data = registration[user_id]
        stage = data.get("stage")
        if stage == "self_endurance":
            data["self_endurance"] = val
            data["stage"] = "self_experience"
            await call.message.edit_text("Оцените ваш Опыт (1-10):", reply_markup=rating_keyboard_1_10)
            return
        if stage == "self_experience":
            data["self_experience"] = val
            data["stage"] = "self_speed"
            await call.message.edit_text("Оцените вашу Скорость (1-10):", reply_markup=rating_keyboard_1_10)
            return
        if stage == "self_speed":
            data["self_speed"] = val
            data["stage"] = "self_technique"
            await call.message.edit_text("Оцените вашу Технику (1-10):", reply_markup=rating_keyboard_1_10)
            return
        if stage == "self_technique":
            data["self_technique"] = val
            # compute self overall rating (average of 4)
            se = data["self_endurance"]
            sx = data["self_experience"]
            ss = data["self_speed"]
            st = data["self_technique"]
            overall = round((se + sx + ss + st) / 4)
            save_player_full(user_id, data["name"], overall, se, sx, ss, st, status='не оценен')
            del registration[user_id]
            await call.message.edit_text("✅ Регистрация завершена\n\nГолосуйте за участие в игре", reply_markup=play_keyboard)
            return

    # If user is in evaluation session
    if user_id in eval_sessions:
        sess = eval_sessions[user_id]
        step = sess.get("step")
        if step == "endurance":
            sess["data"]["endurance"] = val
            sess["step"] = "experience"
            await call.message.edit_text("Оцените Опыт игрока (1-10):", reply_markup=rating_keyboard_1_10)
            return
        if step == "experience":
            sess["data"]["experience"] = val
            sess["step"] = "speed"
            await call.message.edit_text("Оцените Скорость игрока (1-10):", reply_markup=rating_keyboard_1_10)
            return
        if step == "speed":
            sess["data"]["speed"] = val
            sess["step"] = "technique"
            await call.message.edit_text("Оцените Технику игрока (1-10):", reply_markup=rating_keyboard_1_10)
            return
        if step == "technique":
            sess["data"]["technique"] = val
            # store to DB
            target = sess["target"]
            rater = user_id
            d = sess["data"]
            cursor.execute('''INSERT INTO ratings (player_tg_id, rater_tg_id, endurance, experience, speed, technique, created_at)
                              VALUES (?,?,?,?,?,?,?)''', (target, rater, d["endurance"], d["experience"], d["speed"], d["technique"], datetime.utcnow().isoformat()))
            conn.commit()
            # compute stats
            raters_count, avg = compute_peer_stats(target)
            status = 'оценен' if raters_count >= 5 else 'не оценен'
            overall_int = round(avg) if raters_count > 0 else 0
            # Update players table rating and status
            # preserve self columns if present
            p = get_player_full(target)
            if p:
                save_player_full(p[0], p[1], overall_int, p[3], p[4], p[5], p[6], status)

            del eval_sessions[user_id]
            await call.message.edit_text(f"✅ Оценка сохранена. Сейчас оценили: {raters_count} человек. Статус игрока: {status}")
            return

    # otherwise ignore
    await call.answer()

# ---------- LIST PLAYERS TO RATE (Variant 1 UX) ----------

@dp.message(Command("rate"))
async def rate_list(message: types.Message):
    # build a list of registered players
    cursor.execute('SELECT tg_id, name, status FROM players')
    rows = cursor.fetchall()
    if not rows:
        await message.answer("Нет зарегистрированных игроков")
        return
    text = "🔎 Выберите игрока для оценки:\n\n"
    kb = InlineKeyboardMarkup(row_width=1)
    for tg_id, name, status in rows:
        text += f"{name} — {status}\n"
        kb.add(InlineKeyboardButton(text=f"Оценить {name}", callback_data=f"rate_player_{tg_id}"))
    await message.answer(text, reply_markup=kb)

@dp.callback_query(lambda c: c.data and c.data.startswith("rate_player_"))
async def start_eval(call: types.CallbackQuery):
    target = int(call.data.split("_")[2])
    rater = call.from_user.id
    if rater == target:
        await call.answer("Нельзя оценивать себя", show_alert=True)
        return
    # start session
    eval_sessions[rater] = {"target": target, "step": "endurance", "data": {}}
    await call.message.edit_text("Оцените Выносливость игрока (1-10):", reply_markup=rating_keyboard_1_10)

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
    game_message = await message.answer("⚽ Игра\n\nИграют (0/15)", reply_markup=play_keyboard)

# ---------- UPDATE LIST ----------

async def update_list():
    playing = [u for u, v in votes.items() if v == "yes"]
    total_rating = 0
    text = f"⚽ Игра\n\nИграют ({len(playing)}/{MAX_PLAYERS})\n\n"
    for i, user in enumerate(playing, 1):
        player = get_player_full(user)
        if player:
            rating = player[2] if player[2] is not None else 0
            total_rating += rating
            text += f"{i}. {player[1]} ({rating}) — {player[7]}\n"
    text += f"\n📊 Общий рейтинг группы: {total_rating}\n"
    if len(playing) >= MAX_PLAYERS:
        text += "\n⛔ Набор игроков завершён"
        await game_message.edit_text(text)
    else:
        await game_message.edit_text(text, reply_markup=play_keyboard)

# ---------- BUTTON YES / NO ----------

@dp.callback_query(lambda c: c.data == "play_yes")
async def play_yes(call: types.CallbackQuery):
    player = get_player_full(call.from_user.id)
    if not player:
        await call.message.edit_text("❌ Вы не зарегистрированы!\n\nНажмите кнопку ниже для регистрации:", reply_markup=register_keyboard)
        return
    votes[call.from_user.id] = "yes"
    await update_list()

@dp.callback_query(lambda c: c.data == "play_no")
async def play_no(call: types.CallbackQuery):
    votes[call.from_user.id] = "no"
    await update_list()

# ---------- HELPER FUNCTIONS (team balancing) ----------

def team_total(team):
    return sum(p["rating"] for p in team)


def team_average(team):
    if not team:
        return 0
    total = team_total(team)
    return round(total / len(team), 2)


def perfect_balance(players):
    best_diff = 999
    best_team1 = None
    best_team2 = None
    for combo in itertools.combinations(players, len(players) // 2):
        team1 = list(combo)
        team2 = [p for p in players if p not in team1]
        r1 = sum(p["rating"] for p in team1)
        r2 = sum(p["rating"] for p in team2)
        diff = abs(r1 - r2)
        if diff < best_diff:
            best_diff = diff
            best_team1 = team1
            best_team2 = team2
    return best_team1, best_team2, best_diff


def balance_teams(players, num_teams, team_size):
    if num_teams == 2:
        t1, t2, diff = perfect_balance(players)
        return [t1, t2]
    sorted_players = sorted(players, key=lambda p: p["rating"], reverse=True)
    teams = [[] for _ in range(num_teams)]
    for i, player in enumerate(sorted_players):
        team_idx = i % num_teams
        teams[team_idx].append(player)
    return teams

# ---------- CREATE TEAMS ----------

@dp.message(Command("teams"))
async def teams(message: types.Message):
    players = []
    for u, v in votes.items():
        if v == "yes":
            player = get_player_full(u)
            if player:
                # if status != 'оценен' but we have self values, use self average as rating fallback
                rating = player[2] if player[2] is not None and player[2] > 0 else 0
                if rating == 0 and player[3] is not None:
                    rating = round((player[3] + (player[4] or 0) + (player[5] or 0) + (player[6] or 0)) / 4)
                players.append({"name": player[1], "rating": rating})
    if len(players) < 10:
        await message.answer("❗ Нужно минимум 10 игроков")
        return
    text = "⚽ Команды\n\n"
    if len(players) >= 15:
        teams_list = balance_teams(players[:15], 3, 5)
        for i, team in enumerate(teams_list, 1):
            total = team_total(team)
            avg = team_average(team)
            text += f"🔹 Команда {i} ({total} | ср. {avg})\n"
            for p in team:
                text += f"{p['name']} ({p['rating']})\n"
            text += "\n"
    else:
        main_players = players[:10]
        bench = players[10:]
        teams_list = balance_teams(main_players, 2, 5)
        text += "🔵 Команда 1\n"
        for p in teams_list[0]:
            text += p["name"] + "\n"
        text += "\n🔴 Команда 2\n"
        for p in teams_list[1]:
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
            player = get_player_full(u)
            if player:
                rating = player[2] if player[2] is not None and player[2] > 0 else 0
                if rating == 0 and player[3] is not None:
                    rating = round((player[3] + (player[4] or 0) + (player[5] or 0) + (player[6] or 0)) / 4)
                players.append({"name": player[1], "rating": rating})
    players = players[:15]
    teams_list = balance_teams(players, 3, 5)
    text = "⚽ Команды\n\n"
    for i, team in enumerate(teams_list, 1):
        text += f"🔹 Команда {i}\n"
        for p in team:
            text += p["name"] + "\n"
        text += "\n"
    await bot.send_message(chat_id, text)

# ---------- SHUFFLE ----------

@dp.callback_query(lambda c: c.data == "shuffle")
async def shuffle(call: types.CallbackQuery):
    member = await bot.get_chat_member(call.message.chat.id, call.from_user.id)
    if member.status not in ["administrator", "creator"]:
        await call.answer("Только администратор может перемешивать команды", show_alert=True)
        return
    players = []
    for u, v in votes.items():
        if v == "yes":
            player = get_player_full(u)
            if player:
                rating = player[2] if player[2] is not None and player[2] > 0 else 0
                if rating == 0 and player[3] is not None:
                    rating = round((player[3] + (player[4] or 0) + (player[5] or 0) + (player[6] or 0)) / 4)
                players.append({"name": player[1], "rating": rating})
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


if __name__ == '__main__':
    asyncio.run(main())
