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

PUBLISHED_FILE = "published.json"
BAN_FILE = "bans.json"

# Спам-фильтры
FORBIDDEN_WORDS = ["реклама", "раскрутка", "накрутка", "магазин", "скидка", 
                   "заработок", "биткоин", "крипта", "услуги"]
FORBIDDEN_LINKS = ["t.me", "telegram", "instagram", "wa.me", "whatsapp", "youtube"]

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
            total_chars INTEGER DEFAULT 0
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
    conn = sqlite3.connect(DB_PATH)
    conn.execute('INSERT OR REPLACE INTO user_posts (post_id, user_id, published_date, text) VALUES (?, ?, ?, ?)',
                 (post_id, user_id, datetime.now().isoformat(), text[:500]))
    conn.execute('''
        INSERT INTO user_stats (user_id, posts_count, last_post_date, total_chars)
        VALUES (?, 1, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            posts_count = posts_count + 1,
            last_post_date = ?,
            total_chars = total_chars + ?
    ''', (user_id, datetime.now().isoformat(), len(text), datetime.now().isoformat(), len(text)))
    conn.commit()
    conn.close()

def get_user_stats(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    stats = conn.execute('SELECT * FROM user_stats WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    if stats:
        return dict(stats)
    return {"posts_count": 0, "last_post_date": None, "total_chars": 0}

def get_user_posts(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    posts = conn.execute('SELECT post_id, published_date, text FROM user_posts WHERE user_id = ? AND is_deleted = 0 ORDER BY published_date DESC', (user_id,)).fetchall()
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

# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard():
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("📊 Моя статистика", color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button("📝 Мои посты", color=VkKeyboardColor.SECONDARY)
    keyboard.add_button("🗑 Удалить мой пост", color=VkKeyboardColor.NEGATIVE)
    keyboard.add_line()
    keyboard.add_button("🆘 Написать в поддержку", color=VkKeyboardColor.SECONDARY)
    return keyboard

def get_posts_keyboard(posts):
    keyboard = VkKeyboard(one_time=True)
    for i, post in enumerate(posts[:10], 1):
        preview = post['text'][:25] + "..." if len(post['text']) > 25 else post['text']
        keyboard.add_button(f"🗑 Пост #{post['post_id']}", color=VkKeyboardColor.SECONDARY)
        if i % 2 == 0 and i != len(posts[:10]):
            keyboard.add_line()
    keyboard.add_line()
    keyboard.add_button("🔙 Назад", color=VkKeyboardColor.PRIMARY)
    return keyboard

def get_confirm_keyboard(post_id):
    keyboard = VkKeyboard(one_time=True)
    keyboard.add_button("✅ Да, удалить", color=VkKeyboardColor.NEGATIVE)
    keyboard.add_button("❌ Нет", color=VkKeyboardColor.SECONDARY)
    return keyboard

def get_back_keyboard():
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("🔙 Назад в меню", color=VkKeyboardColor.PRIMARY)
    return keyboard

# ========== ФУНКЦИИ ==========
def load_published():
    try:
        with open(PUBLISHED_FILE, "r") as f:
            return json.load(f)
    except:
        return {"published": []}

def save_published(data):
    with open(PUBLISHED_FILE, "w") as f:
        json.dump(data, f)

def load_bans():
    try:
        with open(BAN_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_bans(bans):
    with open(BAN_FILE, "w") as f:
        json.dump(bans, f)

def is_user_banned(user_id):
    bans = load_bans()
    if str(user_id) not in bans:
        return False
    ban_until = datetime.fromisoformat(bans[str(user_id)]["until"])
    if datetime.now() > ban_until:
        del bans[str(user_id)]
        save_bans(bans)
        return False
    return True

def ban_user(user_id, reason):
    bans = load_bans()
    ban_until = datetime.now() + timedelta(hours=BAN_HOURS)
    bans[str(user_id)] = {"until": ban_until.isoformat(), "reason": reason}
    save_bans(bans)

def is_spam(text):
    if not text:
        return False
    text_lower = text.lower()
    for word in FORBIDDEN_WORDS:
        if word in text_lower:
            return True
    for link in FORBIDDEN_LINKS:
        if link in text_lower:
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
        if owner_id and item_id:
            attachments.append(f"{t}{owner_id}_{item_id}")
    return ",".join(attachments) if attachments else None

def get_user_name(vk, user_id):
    try:
        user = vk.users.get(user_ids=user_id)
        return user[0]['first_name'], user[0]['last_name']
    except:
        return "Пользователь", ""

def send_message(vk, user_id, text, keyboard=None):
    try:
        vk.messages.send(user_id=user_id, message=text, random_id=0, 
                        keyboard=keyboard.get_keyboard() if keyboard else None)
    except Exception as e:
        print(f"Ошибка отправки: {e}")

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
                
                if is_user_banned(uid):
                    vk.wall.delete(owner_id=-GROUP_ID, post_id=pid)
                    continue
                
                if is_spam(text):
                    vk.wall.delete(owner_id=-GROUP_ID, post_id=pid)
                    ban_user(uid, "спам")
                    continue
                
                anonymous = contains_anonymous(text)
                clean_text = remove_keywords(text)
                
                if anonymous:
                    final = f"{clean_text}\n\n— Анонимно"
                else:
                    first, last = get_user_name(vk, uid)
                    final = f"{clean_text}\n\n© {first} {last}"
                
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
last_message_text = {}  # Для хранения последнего сообщения пользователя
last_message_post_id = {}  # Для хранения post_id при удалении

def run_messenger():
    vk_session = vk_api.VkApi(token=GROUP_TOKEN, api_version='5.131')
    vk = vk_session.get_api()
    longpoll = VkLongPoll(vk_session, group_id=GROUP_ID)
    
    print("🤖 ЛС бот запущен")
    
    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW and event.to_me:
            user_id = event.user_id
            text = event.text.lower().strip()
            
            # Проверка бана
            if is_user_banned(user_id):
                send_message(vk, user_id, "🚫 Вы забанены.")
                continue
            
            # Режим поддержки
            if user_id in waiting_support:
                if text == "/cancel":
                    waiting_support.discard(user_id)
                    send_message(vk, user_id, "Отменено.", get_main_keyboard())
                else:
                    waiting_support.discard(user_id)
                    send_message(vk, ADMIN_ID, f"Поддержка от {user_id}: {event.text}")
                    send_message(vk, user_id, "✅ Отправлено!", get_main_keyboard())
                continue
            
            # Команды
            if text in ["начать", "меню", "start"]:
                stats = get_user_stats(user_id)
                send_message(vk, user_id,
                    f"👋 Добро пожаловать!\n📊 Постов: {stats['posts_count']}",
                    get_main_keyboard())
            
            elif text == "📊 моя статистика":
                stats = get_user_stats(user_id)
                send_message(vk, user_id,
                    f"📊 Постов: {stats['posts_count']}\n📝 Символов: {stats['total_chars']}",
                    get_main_keyboard())
            
            elif text == "📝 мои посты":
                posts = get_user_posts(user_id)
                if not posts:
                    send_message(vk, user_id, "Нет постов.", get_main_keyboard())
                else:
                    msg = "📝 Ваши посты:\n\n"
                    for i, p in enumerate(posts[:5], 1):
                        date = p['published_date'][:16] if p['published_date'] else "дата неизвестна"
                        preview = p['text'][:40] + "..." if len(p['text']) > 40 else p['text']
                        msg += f"{i}. Пост #{p['post_id']} ({date})\n   {preview}\n\n"
                    send_message(vk, user_id, msg, get_main_keyboard())
            
            elif text == "🗑 удалить мой пост":
                posts = get_user_posts(user_id)
                if not posts:
                    send_message(vk, user_id, "Нет постов для удаления.", get_main_keyboard())
                else:
                    # Отправляем клавиатуру с постами
                    keyboard = get_posts_keyboard(posts)
                    send_message(vk, user_id, "Выберите пост для удаления:", keyboard)
                    # Сохраняем посты для этого пользователя
                    last_message_post_id[user_id] = posts
            
            elif text == "🆘 написать в поддержку":
                waiting_support.add(user_id)
                send_message(vk, user_id, "Напишите сообщение администратору. /cancel для отмены.", get_back_keyboard())
            
            elif text == "🔙 назад в меню":
                send_message(vk, user_id, "Главное меню:", get_main_keyboard())
            
            elif text == "✅ да, удалить" or text.startswith("✅ да"):
                # Обработка подтверждения удаления
                if user_id in last_message_post_id and last_message_post_id[user_id]:
                    # Берем первый пост из списка
                    posts_list = last_message_post_id[user_id]
                    if posts_list:
                        post_to_delete = posts_list[0]
                        post_id = post_to_delete['post_id']
                        if get_post_author(post_id) == user_id:
                            try:
                                vk.wall.delete(owner_id=-GROUP_ID, post_id=post_id)
                                delete_user_post(user_id, post_id)
                                send_message(vk, user_id, f"✅ Пост #{post_id} удален!", get_main_keyboard())
                                last_message_post_id[user_id] = None
                            except Exception as e:
                                send_message(vk, user_id, f"❌ Ошибка: {e}", get_main_keyboard())
                        else:
                            send_message(vk, user_id, "❌ Не ваш пост!", get_main_keyboard())
                    else:
                        send_message(vk, user_id, "Нет постов для удаления.", get_main_keyboard())
                else:
                    send_message(vk, user_id, "Сначала выберите пост для удаления.", get_main_keyboard())
            
            elif text == "❌ нет":
                send_message(vk, user_id, "Удаление отменено.", get_main_keyboard())
                last_message_post_id[user_id] = None
            
            elif text.startswith("🗑 пост #"):
                # Извлекаем ID поста из текста кнопки
                try:
                    post_num = text.split("#")[1].split()[0]
                    post_id = int(post_num)
                    if get_post_author(post_id) == user_id:
                        last_message_post_id[user_id] = [{'post_id': post_id}]
                        send_message(vk, user_id, f"⚠️ Удалить пост #{post_id}?", get_confirm_keyboard(post_id))
                    else:
                        send_message(vk, user_id, "❌ Это не ваш пост!", get_main_keyboard())
                except:
                    send_message(vk, user_id, "Ошибка. Попробуйте снова.", get_main_keyboard())
            
            else:
                send_message(vk, user_id, "Напишите 'Меню' для кнопок", get_main_keyboard())

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    init_db()
    print("✅ База данных готова")
    
    # Запускаем публикатор в фоне
    t = threading.Thread(target=run_publisher, daemon=True)
    t.start()
    
    # Запускаем ЛС бота
    run_messenger()
