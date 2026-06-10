import os
import json
import time
import re
import threading
from datetime import datetime, timedelta

import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from dotenv import load_dotenv

load_dotenv()

# ========== КОНФИГУРАЦИЯ ==========
USER_TOKEN = os.getenv("USER_TOKEN")
GROUP_TOKEN = os.getenv("GROUP_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
PUBLISH_INTERVAL = int(os.getenv("PUBLISH_INTERVAL", "1800"))
BAN_HOURS = int(os.getenv("BAN_HOURS", "24"))
DAILY_POST_LIMIT = int(os.getenv("DAILY_POST_LIMIT", "10"))

PUBLISHED_FILE = "published.json"
BAN_FILE = "bans.json"
USER_LIMITS_FILE = "user_limits.json"

# Спам-фильтры
FORBIDDEN_WORDS = ["реклама", "раскрутка", "накрутка", "магазин", "скидка", 
                   "заработок", "биткоин", "крипта", "услуги"]

# Функция для проверки любых ссылок
def contains_any_link(text):
    if not text:
        return False
    # Проверка на http://, https://, www., и просто домены
    patterns = [
        r'https?://[^\s]+',           # http:// или https://
        r'www\.[^\s]+',               # www.
        r'[a-zA-Z0-9-]+\.[a-zA-Z]{2,}[^\s]*'  # домен типа example.com
    ]
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

# ========== БАЗА ДАННЫХ ==========
import sqlite3
DB_PATH = "posts.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY,
            posts_count INTEGER DEFAULT 0,
            last_post_date TEXT,
            total_chars INTEGER DEFAULT 0,
            daily_posts INTEGER DEFAULT 0,
            last_post_date DATE DEFAULT '',
            temp_limit INTEGER DEFAULT 0,
            temp_limit_until TEXT DEFAULT ''
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_posts (
            post_id INTEGER PRIMARY KEY,
            user_id INTEGER,
            published_date TEXT,
            text TEXT,
            is_deleted BOOLEAN DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def add_post(user_id, post_id, text):
    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute('INSERT OR REPLACE INTO user_posts (post_id, user_id, published_date, text) VALUES (?, ?, ?, ?)',
                 (post_id, user_id, datetime.now().isoformat(), text[:500]))
    conn.execute('''
        INSERT INTO user_stats (user_id, posts_count, last_post_date, total_chars, daily_posts, last_post_date)
        VALUES (?, 1, ?, ?, 1, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            posts_count = posts_count + 1,
            last_post_date = ?,
            total_chars = total_chars + ?,
            daily_posts = CASE WHEN last_post_date = ? THEN daily_posts + 1 ELSE 1 END,
            last_post_date = ?
    ''', (user_id, datetime.now().isoformat(), len(text), today, datetime.now().isoformat(), len(text), today, today))
    conn.commit()
    conn.close()

def get_user_stats(user_id):
    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    stats = conn.execute('SELECT * FROM user_stats WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    
    result = {"posts_count": 0, "last_post_date": None, "total_chars": 0, "daily_posts": 0}
    if stats:
        result = dict(stats)
        # Сброс daily_posts если новый день
        if stats['last_post_date'] != today:
            result['daily_posts'] = 0
    
    # Проверяем временный лимит
    temp_limit = get_temp_limit(user_id)
    if temp_limit:
        result['temp_limit'] = temp_limit['limit']
        result['temp_limit_until'] = temp_limit['until']
    else:
        result['temp_limit'] = 0
    
    return result

def get_temp_limit(user_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute('SELECT temp_limit, temp_limit_until FROM user_stats WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    if row and row[0] > 0:
        until = datetime.fromisoformat(row[1])
        if datetime.now() < until:
            return {"limit": row[0], "until": row[1]}
    return None

def set_temp_limit(user_id, limit):
    until = (datetime.now() + timedelta(hours=24)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE user_stats SET temp_limit = ?, temp_limit_until = ? WHERE user_id = ?', (limit, until, user_id))
    conn.commit()
    conn.close()

def reset_daily_posts(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute('UPDATE user_stats SET daily_posts = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_user_posts(user_id, limit=10):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    posts = conn.execute('''
        SELECT post_id, published_date, text 
        FROM user_posts 
        WHERE user_id = ? AND is_deleted = 0 
        ORDER BY published_date DESC 
        LIMIT ?
    ''', (user_id, limit)).fetchall()
    conn.close()
    return [dict(p) for p in posts]

def delete_user_post(user_id, post_id):
    conn = sqlite3.connect(DB_PATH)
    post = conn.execute('SELECT * FROM user_posts WHERE post_id = ? AND user_id = ?', (post_id, user_id)).fetchone()
    if post:
        conn.execute('UPDATE user_posts SET is_deleted = 1 WHERE post_id = ?', (post_id,))
        conn.execute('UPDATE user_stats SET posts_count = posts_count - 1 WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False

def get_post_author(post_id):
    conn = sqlite3.connect(DB_PATH)
    post = conn.execute('SELECT user_id FROM user_posts WHERE post_id = ?', (post_id,)).fetchone()
    conn.close()
    return post[0] if post else None

def can_user_post(user_id):
    stats = get_user_stats(user_id)
    limit = stats.get('temp_limit', 0)
    if limit > 0:
        current_limit = limit
    else:
        current_limit = DAILY_POST_LIMIT
    
    daily = stats.get('daily_posts', 0)
    return daily < current_limit, current_limit - daily

# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard():
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("📊 Моя статистика", color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button("🗑 Удалить мой пост", color=VkKeyboardColor.NEGATIVE)
    keyboard.add_line()
    keyboard.add_button("🆘 Написать в поддержку", color=VkKeyboardColor.SECONDARY)
    return keyboard

def get_posts_keyboard(posts):
    keyboard = VkKeyboard(one_time=True)
    for i, post in enumerate(posts, 1):
        preview = post['text'][:30] + "..." if len(post['text']) > 30 else post['text']
        keyboard.add_button(f"🗑 Пост #{post['post_id']}: {preview}", color=VkKeyboardColor.SECONDARY)
        if i % 2 == 0 and i != len(posts):
            keyboard.add_line()
    keyboard.add_line()
    keyboard.add_button("🔙 Назад", color=VkKeyboardColor.PRIMARY)
    return keyboard

def get_confirm_keyboard():
    keyboard = VkKeyboard(one_time=True)
    keyboard.add_button("✅ Да, удалить", color=VkKeyboardColor.NEGATIVE)
    keyboard.add_button("❌ Нет", color=VkKeyboardColor.SECONDARY)
    return keyboard

def get_back_keyboard():
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("🔙 Назад в меню", color=VkKeyboardColor.PRIMARY)
    return keyboard

def get_cancel_keyboard():
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("🔙 Отмена", color=VkKeyboardColor.SECONDARY)
    return keyboard

# ========== ФУНКЦИИ ==========
def load_json_file(filepath, default=None):
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except:
        return default if default is not None else {}

def save_json_file(filepath, data):
    with open(filepath, "w") as f:
        json.dump(data, f)

def load_published():
    return load_json_file(PUBLISHED_FILE, {"published": []})

def save_published(data):
    save_json_file(PUBLISHED_FILE, data)

def load_bans():
    return load_json_file(BAN_FILE, {})

def save_bans(bans):
    save_json_file(BAN_FILE, bans)

def get_ban_info(user_id):
    bans = load_bans()
    if str(user_id) not in bans:
        return None
    ban_until = datetime.fromisoformat(bans[str(user_id)]["until"])
    if datetime.now() > ban_until:
        del bans[str(user_id)]
        save_bans(bans)
        return None
    remaining = ban_until - datetime.now()
    hours_left = int(remaining.total_seconds() // 3600)
    minutes_left = int((remaining.total_seconds() % 3600) // 60)
    return {"hours": hours_left, "minutes": minutes_left, "reason": bans[str(user_id)]["reason"]}

def is_user_banned(user_id):
    return get_ban_info(user_id) is not None

def ban_user(user_id, reason):
    bans = load_bans()
    ban_until = datetime.now() + timedelta(hours=BAN_HOURS)
    bans[str(user_id)] = {"until": ban_until.isoformat(), "reason": reason}
    save_bans(bans)

def unban_user(user_id):
    bans = load_bans()
    if str(user_id) in bans:
        del bans[str(user_id)]
        save_bans(bans)
        return True
    return False

def is_spam(text):
    if not text:
        return False
    text_lower = text.lower()
    for word in FORBIDDEN_WORDS:
        if word in text_lower:
            return True
    return False

def contains_anonymous(text):
    keywords = ["анон", "анонимно", "аноним", "#анон", "#анонимно", "#аноним"]
    for kw in keywords:
        if kw in text.lower():
            return True
    return False

def remove_keywords(text):
    keywords = ["анон", "анонимно", "аноним", "#анон", "#анонимно", "#аноним"]
    cleaned = text
    for kw in keywords:
        cleaned = cleaned.replace(kw, "").replace(kw.upper(), "")
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

def build_attachments(post):
    attachments = []
    for a in post.get("attachments", []):
        t = a["type"]
        obj = a[t]
        owner_id = obj.get("owner_id")
        item_id = obj.get("id")
        access_key = obj.get("access_key", "")
        if owner_id and item_id:
            attachment = f"{t}{owner_id}_{item_id}"
            if access_key:
                attachment += f"_{access_key}"
            attachments.append(attachment)
    return ",".join(attachments) if attachments else None

def get_user_name(vk, user_id):
    try:
        user = vk.users.get(user_ids=user_id, fields="first_name,last_name")
        return user[0]['first_name'], user[0]['last_name']
    except:
        return "Пользователь", ""

def make_profile_link(user_id, first_name, last_name):
    return f"[id{user_id}|{first_name} {last_name}]"

def send_message(vk, user_id, text, keyboard=None):
    try:
        vk.messages.send(user_id=user_id, message=text, random_id=0, 
                        keyboard=keyboard.get_keyboard() if keyboard else None)
    except Exception as e:
        print(f"Ошибка отправки: {e}")

def get_attachment_link(vk, attachment):
    """Получает ссылку на вложение"""
    try:
        if attachment.get('type') == 'photo':
            photo = attachment['photo']
            return f"https://vk.com/photo{photo['owner_id']}_{photo['id']}"
        elif attachment.get('type') == 'video':
            video = attachment['video']
            return f"https://vk.com/video{video['owner_id']}_{video['id']}"
        elif attachment.get('type') == 'doc':
            doc = attachment['doc']
            return doc.get('url', f"https://vk.com/doc{doc['owner_id']}_{doc['id']}")
        elif attachment.get('type') == 'sticker':
            sticker = attachment['sticker']
            return f"Стикер ID: {sticker.get('sticker_id')}"
        elif attachment.get('type') == 'wall':
            wall = attachment['wall']
            return f"https://vk.com/wall{wall['from_id']}_{wall['id']}"
        else:
            return f"Вложение типа: {attachment.get('type')}"
    except:
        return "Не удалось получить ссылку на вложение"

def send_to_admin(vk, user_id, message_text, attachments=None):
    if ADMIN_ID:
        try:
            user_info = vk.users.get(user_ids=user_id, fields="first_name,last_name")
            user_name = f"{user_info[0]['first_name']} {user_info[0]['last_name']}"
            user_link = make_profile_link(user_id, user_info[0]['first_name'], user_info[0]['last_name'])
            
            admin_msg = f"📨 Новое сообщение в поддержку\n\n"
            admin_msg += f"👤 От: {user_link}\n"
            admin_msg += f"🆔 ID: {user_id}\n"
            
            if message_text:
                admin_msg += f"💬 Текст:\n{message_text}\n"
            
            if attachments:
                admin_msg += f"\n📎 Вложения:\n"
                for att in attachments:
                    link = get_attachment_link(vk, att)
                    admin_msg += f"   • {link}\n"
            
            admin_msg += f"\n✏️ Чтобы ответить, отправьте сообщение этому пользователю от имени группы"
            
            vk.messages.send(user_id=ADMIN_ID, message=admin_msg, random_id=0)
        except Exception as e:
            print(f"Ошибка отправки админу: {e}")

# ========== ПУБЛИКАТОР ==========
def run_publisher():
    vk = vk_api.VkApi(token=USER_TOKEN).get_api()
    published = load_published()
    last_time = None
    
    print("🚀 Публикатор запущен")
    
    while True:
        try:
            items = vk.wall.get(owner_id=-GROUP_ID, filter="suggests", count=100).get("items", [])
            pending = [p for p in items if p["id"] not in published["published"]]
            
            if pending and (last_time is None or time.time() - last_time >= PUBLISH_INTERVAL):
                post = pending[0]
                pid = post["id"]
                uid = post.get("from_id")
                text = post.get("text", "")
                
                # Проверка на бан
                if is_user_banned(uid):
                    vk.wall.delete(owner_id=-GROUP_ID, post_id=pid)
                    send_message(vk, uid, f"🚫 Ваш пост отклонён. Вы в бане до {get_ban_info(uid)['hours']}ч.")
                    continue
                
                # Проверка на лимит постов в сутки
                can_post, remaining = can_user_post(uid)
                if not can_post:
                    vk.wall.delete(owner_id=-GROUP_ID, post_id=pid)
                    send_message(vk, uid, f"⚠️ Превышен лимит постов ({DAILY_POST_LIMIT} в сутки).\nДоступно сегодня: 0 из {remaining}")
                    continue
                
                # Проверка на спам-слова
                if is_spam(text):
                    vk.wall.delete(owner_id=-GROUP_ID, post_id=pid)
                    ban_user(uid, "спам слова")
                    send_message(vk, uid, f"🚫 Ваш пост отклонён (спам). Вы получили бан 24 часа.")
                    continue
                
                # Проверка на ссылки
                if contains_any_link(text):
                    vk.wall.delete(owner_id=-GROUP_ID, post_id=pid)
                    ban_user(uid, "ссылки запрещены")
                    send_message(vk, uid, f"🚫 Ваш пост отклонён (ссылки запрещены).\nВы получили бан 24 часа.\nПо вопросам разблокировки обратитесь в поддержку.")
                    continue
                
                # Анонимность
                anonymous = contains_anonymous(text)
                clean_text = remove_keywords(text)
                
                if anonymous:
                    final = f"{clean_text}\n\n— Анонимно"
                else:
                    first, last = get_user_name(vk, uid)
                    profile_link = make_profile_link(uid, first, last)
                    final = f"{clean_text}\n\n© {profile_link}"
                
                attachments = build_attachments(post)
                result = vk.wall.post(owner_id=-GROUP_ID, message=final, attachments=attachments, from_group=1)
                
                vk.wall.delete(owner_id=-GROUP_ID, post_id=pid)
                published["published"].append(pid)
                save_published(published)
                add_post(uid, result['post_id'], clean_text)
                last_time = time.time()
                print(f"✅ Пост {pid} опубликован")
                
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"Ошибка публикатора: {e}")
            time.sleep(60)

# ========== ЛС БОТ ==========
waiting_support = set()
selected_post_for_delete = {}

def run_messenger():
    vk_session = vk_api.VkApi(token=GROUP_TOKEN, api_version='5.131')
    vk = vk_session.get_api()
    
    vk_user_session = vk_api.VkApi(token=USER_TOKEN, api_version='5.131')
    vk_user = vk_user_session.get_api()
    
    longpoll = VkLongPoll(vk_session, group_id=GROUP_ID)
    
    print("🤖 ЛС бот запущен")
    
    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW and event.to_me:
            user_id = event.user_id
            text = event.text.lower().strip() if event.text else ""
            
            # Админ-команды
            if user_id == ADMIN_ID and text.startswith("/"):
                if text.startswith("/unban "):
                    target_id = int(text.split()[1])
                    if unban_user(target_id):
                        send_message(vk, user_id, f"✅ Пользователь {target_id} разбанен")
                    else:
                        send_message(vk, user_id, f"❌ Пользователь {target_id} не в бане")
                    continue
                
                elif text.startswith("/setlimit "):
                    parts = text.split()
                    if len(parts) == 3:
                        target_id = int(parts[1])
                        limit = int(parts[2])
                        set_temp_limit(target_id, limit)
                        send_message(vk, user_id, f"✅ Пользователю {target_id} установлен лимит {limit} постов на 24 часа")
                    else:
                        send_message(vk, user_id, f"❌ Формат: /setlimit ID ЛИМИТ")
                    continue
                
                elif text.startswith("/stats "):
                    target_id = int(text.split()[1])
                    stats = get_user_stats(target_id)
                    ban_info = get_ban_info(target_id)
                    msg = f"📊 Статистика пользователя {target_id}\n\n"
                    msg += f"📝 Всего постов: {stats['posts_count']}\n"
                    msg += f"📅 Сегодня: {stats.get('daily_posts', 0)} из {stats.get('temp_limit', DAILY_POST_LIMIT)}\n"
                    if ban_info:
                        msg += f"🚫 Бан: {ban_info['hours']}ч {ban_info['minutes']}м\nПричина: {ban_info['reason']}"
                    else:
                        msg += f"✅ Бан: нет"
                    send_message(vk, user_id, msg)
                    continue
                
                elif text.startswith("/reset "):
                    target_id = int(text.split()[1])
                    reset_daily_posts(target_id)
                    send_message(vk, user_id, f"✅ Счётчик постов для {target_id} сброшен")
                    continue
                
                else:
                    send_message(vk, user_id, "Доступные команды:\n/unban ID\n/setlimit ID ЛИМИТ\n/stats ID\n/reset ID")
                    continue
            
            # Проверка бана
            ban_info = get_ban_info(user_id)
            if ban_info:
                send_message(vk, user_id, f"🚫 Вы забанены на {ban_info['hours']}ч {ban_info['minutes']}м\nПричина: {ban_info['reason']}")
                continue
            
            # Режим поддержки
            if user_id in waiting_support:
                if text == "🔙 отмена" or text == "/cancel":
                    waiting_support.discard(user_id)
                    send_message(vk, user_id, "❌ Отменено.", get_main_keyboard())
                else:
                    waiting_support.discard(user_id)
                    attachments = event.attachments if hasattr(event, 'attachments') else []
                    send_to_admin(vk, user_id, event.text, attachments)
                    send_message(vk, user_id, "✅ Сообщение отправлено администратору!", get_main_keyboard())
                continue
            
            # Команды
            if text in ["начать", "меню", "start"] or not text:
                stats = get_user_stats(user_id)
                send_message(vk, user_id,
                    f"👋 Добро пожаловать!\n📊 Постов: {stats['posts_count']}",
                    get_main_keyboard())
            
            elif text == "📊 моя статистика":
                stats = get_user_stats(user_id)
                ban_info = get_ban_info(user_id)
                current_limit = stats.get('temp_limit', 0)
                if current_limit > 0:
                    limit = current_limit
                    limit_text = f"временно увеличено до {limit}"
                else:
                    limit = DAILY_POST_LIMIT
                    limit_text = f"{limit}"
                
                remaining = limit - stats.get('daily_posts', 0)
                
                msg = f"📊 Ваша статистика\n\n"
                msg += f"📝 Опубликовано всего: {stats['posts_count']}\n"
                msg += f"📅 Доступно сегодня: {remaining} из {limit_text} постов\n"
                
                if current_limit > 0:
                    until = datetime.fromisoformat(stats['temp_limit_until'])
                    hours_left = int((until - datetime.now()).total_seconds() // 3600)
                    msg += f"⏰ Временное увеличение действует ещё {hours_left}ч\n"
                
                if ban_info:
                    msg += f"\n🚫 Блокировка активна!\n   Осталось: {ban_info['hours']}ч {ban_info['minutes']}м\n   Причина: {ban_info['reason']}"
                else:
                    msg += f"\n✅ Блокировки отсутствуют"
                
                send_message(vk, user_id, msg, get_main_keyboard())
            
            elif text == "🗑 удалить мой пост":
                posts = get_user_posts(user_id, limit=10)
                if not posts:
                    send_message(vk, user_id, "📭 У вас нет опубликованных постов.", get_main_keyboard())
                else:
                    keyboard = get_posts_keyboard(posts)
                    send_message(vk, user_id, f"📋 У вас {len(posts)} пост(ов).\nВыберите какой удалить:", keyboard)
            
            elif text == "🆘 написать в поддержку":
                waiting_support.add(user_id)
                send_message(vk, user_id, "📝 Напишите ваше сообщение администратору.\nНажмите «Отмена» чтобы вернуться в меню.", get_cancel_keyboard())
            
            elif text == "🔙 отмена":
                send_message(vk, user_id, "Главное меню:", get_main_keyboard())
            
            elif text == "🔙 назад в меню":
                selected_post_for_delete.pop(user_id, None)
                send_message(vk, user_id, "Главное меню:", get_main_keyboard())
            
            elif text == "❌ нет":
                selected_post_for_delete.pop(user_id, None)
                send_message(vk, user_id, "Удаление отменено.", get_main_keyboard())
            
            elif text == "✅ да, удалить":
                if user_id in selected_post_for_delete:
                    post_id = selected_post_for_delete[user_id]
                    if get_post_author(post_id) == user_id:
                        try:
                            vk_user.wall.delete(owner_id=-GROUP_ID, post_id=post_id)
                            delete_user_post(user_id, post_id)
                            send_message(vk, user_id, f"✅ Пост #{post_id} удален!", get_main_keyboard())
                            selected_post_for_delete.pop(user_id, None)
                        except Exception as e:
                            send_message(vk, user_id, f"❌ Ошибка удаления: {e}", get_main_keyboard())
                    else:
                        send_message(vk, user_id, "❌ Это не ваш пост!", get_main_keyboard())
                else:
                    send_message(vk, user_id, "Сначала выберите пост для удаления.", get_main_keyboard())
            
            elif text.startswith("🗑 пост #"):
                try:
                    match = re.search(r'#(\d+)', text)
                    if match:
                        post_id = int(match.group(1))
                        if get_post_author(post_id) == user_id:
                            selected_post_for_delete[user_id] = post_id
                            send_message(vk, user_id, f"⚠️ Удалить пост #{post_id}?", get_confirm_keyboard())
                        else:
                            send_message(vk, user_id, "❌ Это не ваш пост!", get_main_keyboard())
                    else:
                        send_message(vk, user_id, "Ошибка. Попробуйте снова.", get_main_keyboard())
                except:
                    send_message(vk, user_id, "Ошибка. Попробуйте снова.", get_main_keyboard())
            
            else:
                # Неизвестная команда — показываем клавиатуру
                send_message(vk, user_id, "Нажмите на кнопку в меню", get_main_keyboard())

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    init_db()
    print("✅ База данных готова")
    
    # Сброс daily_posts для всех (если новый день)
    today = datetime.now().date().isoformat()
    
    # Запускаем публикатор в фоне
    t = threading.Thread(target=run_publisher, daemon=True)
    t.start()
    
    # Запускаем ЛС бота
    run_messenger()
