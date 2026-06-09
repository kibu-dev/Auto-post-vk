import os
import json
import time
import re
import traceback
import threading
from datetime import datetime, timedelta

import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from dotenv import load_dotenv

load_dotenv()

# ========== КОНФИГУРАЦИЯ ==========
USER_TOKEN = os.getenv("USER_TOKEN")
GROUP_TOKEN = os.getenv("GROUP_TOKEN")  # Токен сообщества для ЛС
GROUP_ID = int(os.getenv("GROUP_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))
PUBLISH_INTERVAL = int(os.getenv("PUBLISH_INTERVAL", "1800"))
BAN_HOURS = int(os.getenv("BAN_HOURS", "24"))

PUBLISHED_FILE = "published.json"
BAN_FILE = "bans.json"

# Спам-фильтры
FORBIDDEN_WORDS = [
    "реклама", "реклам", "раскрутка", "накрутка",
    "магазин", "скидка", "акция", "распродажа",
    "заработок", "заработать", "биткоин", "крипта",
    "бесплатно", "предложение", "услуги", "услуг"
]

FORBIDDEN_LINKS = [
    "vk.com/app", "vk.com/market", "t.me", "telegram",
    "instagram", "instagram.com", "wa.me", "whatsapp",
    "youtube", "youtu.be"
]

# ========== БАЗА ДАННЫХ (SQLite) ==========
import sqlite3
from contextlib import contextmanager

DB_PATH = "posts.db"

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
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
        conn.execute('CREATE INDEX IF NOT EXISTS idx_user_posts_user_id ON user_posts(user_id)')

def add_post(user_id, post_id, text):
    with get_db() as conn:
        conn.execute('''
            INSERT OR REPLACE INTO user_posts (post_id, user_id, published_date, text)
            VALUES (?, ?, ?, ?)
        ''', (post_id, user_id, datetime.now().isoformat(), text[:500]))
        conn.execute('''
            INSERT INTO user_stats (user_id, posts_count, last_post_date, total_chars)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                posts_count = posts_count + 1,
                last_post_date = ?,
                total_chars = total_chars + ?
        ''', (user_id, datetime.now().isoformat(), len(text), datetime.now().isoformat(), len(text)))

def get_user_stats(user_id):
    with get_db() as conn:
        stats = conn.execute('SELECT * FROM user_stats WHERE user_id = ?', (user_id,)).fetchone()
        if stats:
            return dict(stats)
        return {"posts_count": 0, "last_post_date": None, "total_chars": 0}

def get_user_posts(user_id):
    with get_db() as conn:
        posts = conn.execute('''
            SELECT post_id, published_date, text, is_deleted 
            FROM user_posts 
            WHERE user_id = ? AND is_deleted = 0
            ORDER BY published_date DESC
        ''', (user_id,)).fetchall()
        return [dict(p) for p in posts]

def delete_user_post(user_id, post_id):
    with get_db() as conn:
        post = conn.execute('SELECT * FROM user_posts WHERE post_id = ? AND user_id = ?', 
                           (post_id, user_id)).fetchone()
        if post:
            conn.execute('UPDATE user_posts SET is_deleted = 1 WHERE post_id = ?', (post_id,))
            conn.execute('UPDATE user_stats SET posts_count = posts_count - 1 WHERE user_id = ?', (user_id,))
            return True
        return False

def get_post_author(post_id):
    with get_db() as conn:
        post = conn.execute('SELECT user_id FROM user_posts WHERE post_id = ?', (post_id,)).fetchone()
        return post["user_id"] if post else None

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
    for i, post in enumerate(posts[:10], 1):
        post_preview = post['text'][:30] + "..." if len(post['text']) > 30 else post['text']
        keyboard.add_button(f"🗑 Пост #{post['post_id']}", color=VkKeyboardColor.SECONDARY, 
                           payload={"post_id": post['post_id']})
        if i % 2 == 0 and i != len(posts[:10]):
            keyboard.add_line()
    keyboard.add_line()
    keyboard.add_button("🔙 Назад", color=VkKeyboardColor.PRIMARY, payload={"action": "back"})
    return keyboard

def get_confirm_keyboard(post_id):
    keyboard = VkKeyboard(one_time=True)
    keyboard.add_button("✅ Да, удалить", color=VkKeyboardColor.NEGATIVE, 
                       payload={"action": "confirm_delete", "post_id": post_id})
    keyboard.add_button("❌ Нет", color=VkKeyboardColor.SECONDARY, payload={"action": "cancel"})
    return keyboard

def get_back_keyboard():
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("🔙 Назад в меню", color=VkKeyboardColor.PRIMARY, payload={"action": "back"})
    return keyboard

# ========== ФУНКЦИИ ДЛЯ ПУБЛИКАТОРА ==========
def load_published():
    try:
        with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"published": []}

def save_published(data):
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_bans():
    try:
        with open(BAN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_bans(bans):
    with open(BAN_FILE, "w", encoding="utf-8") as f:
        json.dump(bans, f, ensure_ascii=False, indent=2)

def is_user_banned(user_id):
    bans = load_bans()
    if str(user_id) not in bans:
        return False
    ban_info = bans[str(user_id)]
    ban_until = datetime.fromisoformat(ban_info["until"])
    if datetime.now() > ban_until:
        del bans[str(user_id)]
        save_bans(bans)
        return False
    return True

def ban_user(user_id, reason):
    bans = load_bans()
    ban_until = datetime.now() + timedelta(hours=BAN_HOURS)
    bans[str(user_id)] = {"until": ban_until.isoformat(), "reason": reason, "banned_at": datetime.now().isoformat()}
    save_bans(bans)
    print(f"⚠️ Пользователь {user_id} забанен на {BAN_HOURS}ч. Причина: {reason}")

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

def contains_forbidden_words(text):
    if not text:
        return False
    text_lower = text.lower()
    for word in FORBIDDEN_WORDS:
        if re.search(r'\b' + re.escape(word) + r'\b', text_lower):
            return True
    return False

def contains_forbidden_links(text):
    if not text:
        return False
    text_lower = text.lower()
    for link in FORBIDDEN_LINKS:
        if link in text_lower:
            return True
    return False

def is_spam(text):
    if contains_forbidden_words(text):
        return True, "запрещенные слова"
    if contains_forbidden_links(text):
        return True, "ссылки на сторонние ресурсы"
    return False, None

def delete_suggestion(vk, group_id, suggestion_id, from_id):
    try:
        vk.wall.delete(owner_id=-group_id, post_id=suggestion_id)
        return True
    except:
        return False

def get_user_full_name(vk, user_id):
    try:
        user = vk.users.get(user_ids=user_id, fields="first_name,last_name")
        if user and len(user) > 0:
            return user[0].get('first_name', ''), user[0].get('last_name', '')
    except:
        pass
    return "", ""

def contains_anonymous_keyword(text):
    keywords = ["анон", "анонимно", "аноним", "#анон", "#анонимно", "#аноним"]
    for keyword in keywords:
        if re.search(r'\b' + re.escape(keyword) + r'\b', text, re.IGNORECASE):
            return True
    return False

def remove_keywords(text):
    keywords = ["анон", "анонимно", "аноним", "#анон", "#анонимно", "#аноним"]
    cleaned = text
    for keyword in keywords:
        cleaned = re.sub(r'\b' + re.escape(keyword) + r'\b', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = re.sub(r'\n\s*\n', '\n\n', cleaned)
    return cleaned

def make_profile_link(user_id, first_name, last_name):
    return f"[id{user_id}|{first_name} {last_name}]"

def can_publish_now(last_publish_time, publish_interval):
    if last_publish_time is None:
        return True
    return (time.time() - last_publish_time) >= publish_interval

# ========== ФУНКЦИИ ДЛЯ ЛС БОТА ==========
def check_group_subscription(vk, user_id):
    try:
        return vk.groups.isMember(group_id=GROUP_ID, user_id=user_id)
    except:
        return False

def send_message(vk, user_id, message, keyboard=None):
    try:
        vk.messages.send(
            user_id=user_id,
            message=message,
            random_id=0,
            keyboard=keyboard.get_keyboard() if keyboard else None
        )
    except Exception as e:
        print(f"Ошибка отправки: {e}")

def send_to_admin(vk, message):
    if ADMIN_ID:
        try:
            vk.messages.send(user_id=ADMIN_ID, message=message, random_id=0)
        except:
            pass

# ========== ПОТОК ДЛЯ ПУБЛИКАЦИИ ПОСТОВ ==========
def run_publisher():
    vk_session = vk_api.VkApi(token=USER_TOKEN)
    vk = vk_session.get_api()
    published = load_published()
    last_publish_time = None
    
    print("=" * 60)
    print("🚀 ПОТОК ПУБЛИКАЦИИ ЗАПУЩЕН")
    print(f"📌 Группа: -{GROUP_ID}")
    print(f"⏱ Интервал: {PUBLISH_INTERVAL // 60} мин.")
    print("=" * 60 + "\n")
    
    while True:
        try:
            response = vk.wall.get(owner_id=-GROUP_ID, filter="suggests", count=100)
            items = response.get("items", [])
            
            pending_posts = [p for p in items if p["id"] not in published["published"]]
            
            if pending_posts and can_publish_now(last_publish_time, PUBLISH_INTERVAL):
                post = pending_posts[0]
                post_id = post["id"]
                from_id = post.get("from_id")
                original_text = post.get("text", "")
                
                if is_user_banned(from_id):
                    delete_suggestion(vk, GROUP_ID, post_id, from_id)
                    continue
                
                is_spam_post, spam_reason = is_spam(original_text)
                if is_spam_post:
                    delete_suggestion(vk, GROUP_ID, post_id, from_id)
                    ban_user(from_id, spam_reason)
                    continue
                
                is_anonymous = contains_anonymous_keyword(original_text)
                cleaned_text = remove_keywords(original_text)
                
                if is_anonymous:
                    final_text = f"{cleaned_text}\n\n— Анонимно"
                else:
                    first_name, last_name = get_user_full_name(vk, from_id)
                    if first_name and last_name:
                        profile_link = make_profile_link(from_id, first_name, last_name)
                        final_text = f"{cleaned_text}\n\n© {profile_link}"
                    else:
                        final_text = f"{cleaned_text}\n\n— Анонимно"
                
                attachments = build_attachments(post)
                result = vk.wall.post(owner_id=-GROUP_ID, message=final_text, attachments=attachments, from_group=1)
                
                print(f"✅ Пост #{post_id} опубликован!")
                last_publish_time = time.time()
                
                delete_suggestion(vk, GROUP_ID, post_id, from_id)
                published["published"].append(post_id)
                save_published(published)
                add_post(from_id, result['post_id'], cleaned_text)
                
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"Ошибка публикатора: {e}")
            time.sleep(60)

# ========== ПОТОК ДЛЯ ОБРАБОТКИ ЛС ==========
def run_messenger():
    # Используем токен сообщества с правильной версией API
    vk_session = vk_api.VkApi(token=GROUP_TOKEN, api_version='5.131')
    vk = vk_session.get_api()
    
    # Проверяем токен
    try:
        vk.messages.getConversations(count=1)
        print("✅ ЛС бот успешно авторизован")
    except Exception as e:
        print(f"❌ Ошибка авторизации ЛС бота: {e}")
        print("Проверь GROUP_TOKEN и права на сообщения")
        return
    
    # LongPoll для сообщества - обязательно указываем group_id!
    try:
        longpoll = VkLongPoll(vk_session, group_id=GROUP_ID)
        print("✅ LongPoll для сообщества подключен")
    except Exception as e:
        print(f"❌ Ошибка подключения LongPoll: {e}")
        return
    
    waiting_for_support = set()
    
    print("=" * 60)
    print("🤖 ПОТОК ЛС БОТА ЗАПУЩЕН")
    print("=" * 60 + "\n")
    
    for event in longpoll.listen():
        try:
            if event.type == VkEventType.MESSAGE_NEW and event.to_me:
                user_id = event.user_id
                message_text = event.text.lower().strip() if event.text else ""
                payload = event.payload
                
                # Проверка подписки
                if not check_group_subscription(vk, user_id):
                    send_message(vk, user_id, 
                        f"❌ Вы не подписаны на сообщество!\nhttps://vk.com/club{GROUP_ID}",
                        get_back_keyboard())
                    continue
                
                # Проверка бана
                if is_user_banned(user_id):
                    send_message(vk, user_id, "🚫 Вы забанены за нарушение правил.", get_back_keyboard())
                    continue
                
                # Режим ожидания сообщения в поддержку
                if user_id in waiting_for_support:
                    if message_text == "/cancel":
                        waiting_for_support.discard(user_id)
                        send_message(vk, user_id, "❌ Отменено.", get_main_keyboard())
                    else:
                        waiting_for_support.discard(user_id)
                        try:
                            user_info = vk.users.get(user_ids=user_id)[0]
                            admin_msg = f"📨 Поддержка\nОт: {user_info['first_name']} (id{user_id})\nТекст: {event.text}"
                            send_to_admin(vk, admin_msg)
                            send_message(vk, user_id, "✅ Сообщение отправлено администратору!", get_main_keyboard())
                        except Exception as e:
                            print(f"Ошибка отправки в поддержку: {e}")
                            send_message(vk, user_id, "❌ Ошибка отправки. Попробуйте позже.", get_main_keyboard())
                    continue
                
                # Обработка команд
                if message_text in ["начать", "меню", "start"]:
                    stats = get_user_stats(user_id)
                    send_message(vk, user_id,
                        f"👋 Добро пожаловать!\n\n📊 Постов: {stats['posts_count']}\n📝 Символов: {stats['total_chars']}",
                        get_main_keyboard())
                
                elif message_text == "📊 моя статистика":
                    stats = get_user_stats(user_id)
                    send_message(vk, user_id,
                        f"📊 Ваша статистика\n\nПостов: {stats['posts_count']}\nСимволов: {stats['total_chars']}",
                        get_main_keyboard())
                
                elif message_text == "🗑 удалить мой пост":
                    posts = get_user_posts(user_id)
                    if not posts:
                        send_message(vk, user_id, "📭 Нет постов для удаления", get_main_keyboard())
                    else:
                        send_message(vk, user_id, "🗑 Выберите пост:", get_posts_keyboard(posts))
                
                elif message_text == "🆘 написать в поддержку":
                    waiting_for_support.add(user_id)
                    send_message(vk, user_id, "📝 Напишите сообщение администратору. /cancel для отмены", get_back_keyboard())
                
                elif message_text == "🔙 назад в меню":
                    send_message(vk, user_id, "Главное меню:", get_main_keyboard())
                
                # Обработка payload (нажатий на кнопки)
                elif payload:
                    import json as json_module
                    try:
                        payload_data = json_module.loads(payload) if isinstance(payload, str) else payload
                        if payload_data.get("action") == "back":
                            send_message(vk, user_id, "Главное меню:", get_main_keyboard())
                        elif payload_data.get("action") == "confirm_delete":
                            post_id = payload_data.get("post_id")
                            if get_post_author(post_id) == user_id:
                                try:
                                    vk.wall.delete(owner_id=-GROUP_ID, post_id=post_id)
                                    delete_user_post(user_id, post_id)
                                    send_message(vk, user_id, f"✅ Пост #{post_id} удален!", get_main_keyboard())
                                except Exception as e:
                                    send_message(vk, user_id, f"❌ Ошибка удаления: {e}", get_main_keyboard())
                            else:
                                send_message(vk, user_id, "❌ Это не ваш пост!", get_main_keyboard())
                        elif payload_data.get("post_id"):
                            post_id = payload_data.get("post_id")
                            send_message(vk, user_id, 
                                f"⚠️ Удалить пост #{post_id}?", 
                                get_confirm_keyboard(post_id))
                    except:
                        pass
                else:
                    send_message(vk, user_id, "❌ Неизвестная команда. Нажмите на кнопки.", get_main_keyboard())
                    
        except Exception as e:
            print(f"Ошибка обработки сообщения: {e}")
            traceback.print_exc()

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    init_db()
    print("✅ База данных инициализирована")
    
    # Запускаем оба потока
    publisher_thread = threading.Thread(target=run_publisher, daemon=True)
    messenger_thread = threading.Thread(target=run_messenger, daemon=True)
    
    publisher_thread.start()
    messenger_thread.start()
    
    print("✅ Оба потока запущены!")
    print("🔄 Бот работает...\n")
    
    # Держим главный поток живым
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
