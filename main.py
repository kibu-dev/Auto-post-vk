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

# Конфигурация
USER_TOKEN = os.getenv("USER_TOKEN")
GROUP_TOKEN = os.getenv("GROUP_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
PUBLISH_INTERVAL = int(os.getenv("PUBLISH_INTERVAL", "1800"))
BAN_HOURS = int(os.getenv("BAN_HOURS", "24"))

PUBLISHED_FILE = "published.json"
BAN_FILE = "bans.json"

# Спам-фильтры (только для слов)
FORBIDDEN_WORDS = [
    "реклама",
    "раскрутка",
    "накрутка",
    "магазин",
    "скидка",
    "заработок",
    "биткоин",
    "крипта",
    "услуги",
]

# Функция проверки наличия любых ссылок
def contains_any_link(text):
    if not text:
        return False
    patterns = [
        r'https?://[^\s]+',
        r'www\.[^\s]+',
        r'[a-zA-Z0-9-]+\.[a-zA-Z]{2,}[^\s]*'
    ]
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

# База данных
import sqlite3
DB_PATH = "posts.db"

def init_db():
    """Инициализация базы данных."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY,
            posts_count INTEGER DEFAULT 0,
            last_post_date TEXT,
            total_chars INTEGER DEFAULT 0
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_posts (
            post_id INTEGER PRIMARY KEY,
            user_id INTEGER,
            published_date TEXT,
            text TEXT,
            is_deleted BOOLEAN DEFAULT 0
        );
        """
    )
    conn.commit()
    conn.close()

def add_post(user_id, post_id, text):
    """Добавление поста в базу данных."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO user_posts (post_id, user_id, published_date, text) "
        "VALUES (?, ?, ?, ?)",
        (post_id, user_id, datetime.now().isoformat(), text[:500]),
    )
    conn.execute(
        """
        INSERT INTO user_stats (user_id, posts_count, last_post_date, total_chars)
        VALUES (?, 1, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            posts_count = posts_count + 1,
            last_post_date = ?,
            total_chars = total_chars + ?;
        """,
        (user_id, datetime.now().isoformat(), len(text), datetime.now().isoformat(), len(text)),
    )
    conn.commit()
    conn.close()

def get_user_stats(user_id):
    """Получение статистики пользователя."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    stats = conn.execute("SELECT * FROM user_stats WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(stats) if stats else {
        "posts_count": 0,
        "last_post_date": None,
        "total_chars": 0,
    }

def get_user_posts(user_id):
    """Получение постов пользователя."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    posts = conn.execute(
        "SELECT post_id, published_date, text FROM user_posts "
        "WHERE user_id = ? AND is_deleted = 0 ORDER BY published_date DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(p) for p in posts]

def delete_user_post(user_id, post_id):
    """Удаление поста пользователя."""
    conn = sqlite3.connect(DB_PATH)
    post = conn.execute(
        "SELECT * FROM user_posts WHERE post_id = ? AND user_id = ?", (post_id, user_id)
    ).fetchone()
    if post:
        conn.execute("UPDATE user_posts SET is_deleted = 1 WHERE post_id = ?", (post_id,))
        conn.execute("UPDATE user_stats SET posts_count = posts_count - 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False

def get_post_author(post_id):
    """Получение автора поста."""
    conn = sqlite3.connect(DB_PATH)
    post = conn.execute("SELECT user_id FROM user_posts WHERE post_id = ?", (post_id,)).fetchone()
    conn.close()
    return post[0] if post else None

# Клавиатуры
def get_main_keyboard():
    """Главная клавиатура."""
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("📊 Моя статистика", color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button("🗑 Удалить мой пост", color=VkKeyboardColor.NEGATIVE)
    keyboard.add_line()
    keyboard.add_button("🆘 Написать в поддержку", color=VkKeyboardColor.SECONDARY)
    return keyboard

def get_posts_keyboard(posts):
    """Клавиатура выбора поста для удаления."""
    keyboard = VkKeyboard(one_time=True)
    for i, post in enumerate(posts[:10], 1):
        preview = post["text"][:35] + "..." if len(post["text"]) > 35 else post["text"]
        keyboard.add_button(f"🗑 Пост #{post['post_id']}: {preview}", color=VkKeyboardColor.SECONDARY)
        if i % 2 == 0 and i != len(posts[:10]):
            keyboard.add_line()
    keyboard.add_line()
    keyboard.add_button("🔙 Назад", color=VkKeyboardColor.PRIMARY)
    return keyboard

def get_confirm_keyboard():
    """Подтверждение удаления поста."""
    keyboard = VkKeyboard(one_time=True)
    keyboard.add_button("✅ Да, удалить", color=VkKeyboardColor.NEGATIVE)
    keyboard.add_button("❌ Нет", color=VkKeyboardColor.SECONDARY)
    return keyboard

def get_back_keyboard():
    """Кнопка возврата в главное меню."""
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("🔙 Назад в меню", color=VkKeyboardColor.PRIMARY)
    return keyboard

def get_cancel_keyboard():
    """Кнопка отмены действия."""
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("🔙 Отмена", color=VkKeyboardColor.SECONDARY)
    return keyboard

# Функции
def load_published():
    """Загрузка списка опубликованных постов."""
    try:
        with open(PUBLISHED_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"published": []}

def save_published(data):
    """Сохранение списка опубликованных постов."""
    with open(PUBLISHED_FILE, "w") as f:
        json.dump(data, f)

def load_bans():
    """Загрузка списка банов пользователей."""
    try:
        with open(BAN_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_bans(bans):
    """Сохранение списка банов пользователей."""
    with open(BAN_FILE, "w") as f:
        json.dump(bans, f)

def get_ban_info(user_id):
    """Проверка статуса блокировки пользователя."""
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
    return {
        "hours": hours_left,
        "minutes": minutes_left,
        "reason": bans[str(user_id)]["reason"],
    }

def is_user_banned(user_id):
    """Проверка, заблокирован ли пользователь."""
    return get_ban_info(user_id) is not None

def ban_user(user_id, reason):
    """Блокировка пользователя."""
    bans = load_bans()
    ban_until = datetime.now() + timedelta(hours=BAN_HOURS)
    bans[str(user_id)] = {"until": ban_until.isoformat(), "reason": reason}
    save_bans(bans)

def is_spam(text):
    """Проверка текста на спам-слова."""
    if not text:
        return False
    text_lower = text.lower()
    for word in FORBIDDEN_WORDS:
        if word in text_lower:
            return True
    return False

def contains_anonymous(text):
    """Проверка текста на анонимность."""
    keywords = ["анон", "анонимно", "аноним", "#анон", "#анонимно", "#аноним"]
    for kw in keywords:
        if kw in text.lower():
            return True
    return False

def remove_keywords(text):
    """Удаление ключевых слов из текста."""
    keywords = ["анон", "анонимно", "аноним", "#анон", "#анонимно", "#аноним"]
    cleaned = text
    for kw in keywords:
        cleaned = cleaned.replace(kw, "").replace(kw.upper(), "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned

def build_attachments(post):
    """Создание списка вложений для публикации."""
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
    """Получение имени пользователя."""
    try:
        user = vk.users.get(user_ids=user_id, fields="first_name,last_name")[0]
        return user["first_name"], user["last_name"]
    except Exception:
        return "Пользователь", ""

def send_message(vk, user_id, text, keyboard=None):
    """Отправка сообщения пользователю."""
    try:
        vk.messages.send(
            user_id=user_id,
            message=text,
            random_id=0,
            keyboard=keyboard.get_keyboard() if keyboard else None,
        )
    except Exception as e:
        print(f"Ошибка отправки: {e}")

# Публикатор
def run_publisher():
    """Основной цикл публикатора."""
    vk = vk_api.VkApi(token=USER_TOKEN).get_api()
    published = load_published()
    last_time = None

    print("🚀 Публикатор запущен")

    while True:
        try:
            items = vk.wall.get(owner_id=-GROUP_ID, filter="suggests", count=100)["items"]
            pending = [p for p in items if p["id"] not in published["published"]]

            if pending and (last_time is None or time.time() - last_time >= PUBLISH_INTERVAL):
                post = pending[0]
                pid = post["id"]
                uid = post.get("from_id")
                text = post.get("text", "")

                # Проверка на бан
                if is_user_banned(uid):
                    vk.wall.delete(owner_id=-GROUP_ID, post_id=pid)
                    continue

                # Проверка на спам-слова (баним)
                if is_spam(text):
                    vk.wall.delete(owner_id=-GROUP_ID, post_id=pid)
                    ban_user(uid, "Спам/Реклама")
                    continue

                # Проверка на ссылки (НЕ УДАЛЯЕМ, уведомляем админа)
                if contains_any_link(text):
                    if ADMIN_ID:
                        try:
                            # Получаем имя пользователя
                            try:
                                user_info = vk.users.get(user_ids=uid, fields="first_name,last_name")[0]
                                user_name = f"{user_info['first_name']} {user_info['last_name']}"
                            except:
                                user_name = "Неизвестный"
                            
                            # Определяем анонимность
                            is_anon = contains_anonymous(text)
                            if is_anon:
                                author_text = "Автор: Аноним"
                            else:
                                author_text = f"Автор: {user_name}"
                            
                            # Ссылка на пост в предложках (прямая ссылка на модерацию)
                            post_link = f"https://vk.com/wall-{GROUP_ID}_{pid}?w=wall-{GROUP_ID}_{pid}"
                            
                            admin_msg = f"🚨 ПОДОЗРИТЕЛЬНЫЙ ПОСТ\n\n{author_text}\n\nТекст:\n{text}\n\nСсылка для модерации:\n{post_link}"
                            
                            vk_user_for_notify = vk_api.VkApi(token=USER_TOKEN, api_version='5.131').get_api()
                            vk_user_for_notify.messages.send(user_id=ADMIN_ID, message=admin_msg, random_id=0)
                            print(f"✅ Уведомление админу отправлено (пост {pid})")
                        except Exception as e:
                            print(f"❌ Ошибка отправки админу: {e}")
                    
                    print(f"⚠️ Пост {pid} содержит ссылки, оставлен на модерацию")
                    continue

                # Анонимность
                anonymous = contains_anonymous(text)
                clean_text = remove_keywords(text)

                if anonymous:
                    final = f"{clean_text}\n\nАвтор: Аноним"
                else:
                    first, last = get_user_name(vk, uid)
                    final = f"{clean_text}\n\nАвтор: {first} {last}"

                attachments = build_attachments(post)
                result = vk.wall.post(
                    owner_id=-GROUP_ID,
                    message=final,
                    attachments=attachments,
                    from_group=1,
                )

                vk.wall.delete(owner_id=-GROUP_ID, post_id=pid)
                published["published"].append(result["post_id"])
                save_published(published)
                add_post(uid, result["post_id"], clean_text)
                last_time = time.time()
                print(f"✅ Пост {pid} опубликован")

            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"Ошибка публикатора: {e}")
            time.sleep(60)

# ЛС Бот
waiting_support = set()
selected_post_for_delete = {}

def run_messenger():
    """Основной цикл мессенджера."""
    vk_session = vk_api.VkApi(token=GROUP_TOKEN, api_version="5.131")
    vk = vk_session.get_api()

    vk_user_session = vk_api.VkApi(token=USER_TOKEN, api_version="5.131")
    vk_user = vk_user_session.get_api()

    longpoll = VkLongPoll(vk_session, group_id=GROUP_ID, mode=2, preload_messages=True)

    print("🤖 ЛС бот запущен")

    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW and event.to_me:
            user_id = event.user_id
            text = event.text.strip().lower() if event.text else ""

            ban_info = get_ban_info(user_id)
            if ban_info:
                send_message(
                    vk,
                    user_id,
                    f"🚫 Вы забанены на {ban_info['hours']}ч {ban_info['minutes']}м.\nПричина: {ban_info['reason']}",
                )
                continue

            if user_id in waiting_support:
                if text == "🔙 отмена" or text == "/cancel":
                    waiting_support.discard(user_id)
                    send_message(vk, user_id, "❌ Отменено.", get_main_keyboard())
                else:
                    waiting_support.discard(user_id)
                    
                    # Получаем ID сообщения для пересылки
                    msg_id = event.message_id if hasattr(event, 'message_id') else event.id
                    
                    # Отправляем админу (поддержка)
                    if ADMIN_ID:
                        try:
                            vk.messages.send(
                                user_id=ADMIN_ID,
                                message="📨 Новое обращение в поддержку",
                                random_id=0,
                                forward_messages=msg_id,
                                group_id=GROUP_ID
                            )
                            send_message(vk, user_id, "✅ Сообщение отправлено администратору!", get_main_keyboard())
                        except Exception as e:
                            print(f"Ошибка пересылки: {e}")
                            send_message(vk, user_id, "❌ Ошибка при отправке. Попробуйте позже.", get_main_keyboard())
                continue

            if text in ["начать", "меню", "start"]:
                stats = get_user_stats(user_id)
                send_message(
                    vk,
                    user_id,
                    f"👋 Добро пожаловать!\n📊 Постов: {stats['posts_count']}",
                    get_main_keyboard(),
                )

            elif text == "📊 моя статистика":
                stats = get_user_stats(user_id)
                ban_info = get_ban_info(user_id)
                msg = "📊 Ваша статистика\n\n"
                msg += f"📝 Опубликовано постов: {stats['posts_count']}\n"
                if ban_info:
                    msg += f"\n🚫 Блокировка активна!\nОсталось: {ban_info['hours']}ч {ban_info['minutes']}м\nПричина: {ban_info['reason']}"
                else:
                    msg += "\n✅ Блокировки отсутствуют"
                send_message(vk, user_id, msg, get_main_keyboard())

            elif text == "🗑 удалить мой пост":
                posts = get_user_posts(user_id)
                if not posts:
                    send_message(vk, user_id, "📭 У вас нет опубликованных постов.", get_main_keyboard())
                else:
                    keyboard = get_posts_keyboard(posts)
                    send_message(
                        vk,
                        user_id,
                        f"📋 У вас {len(posts)} пост(ов).\nВыберите, какой удалить:",
                        keyboard,
                    )

            elif text == "🆘 написать в поддержку":
                waiting_support.add(user_id)
                send_message(
                    vk,
                    user_id,
                    "📝 Напишите ваше сообщение администратору.\nНажмите «Отмена» чтобы вернуться в меню.",
                    get_cancel_keyboard(),
                )

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
                    author = get_post_author(post_id)
                    if author == user_id:
                        try:
                            vk_user.wall.delete(owner_id=-GROUP_ID, post_id=post_id)
                            delete_user_post(user_id, post_id)
                            send_message(
                                vk,
                                user_id,
                                f"✅ Пост #{post_id} удален!",
                                get_main_keyboard(),
                            )
                            selected_post_for_delete.pop(user_id, None)
                        except Exception as e:
                            send_message(
                                vk,
                                user_id,
                                f"❌ Ошибка удаления: {e}",
                                get_main_keyboard(),
                            )
                    else:
                        send_message(vk, user_id, "❌ Это не ваш пост!", get_main_keyboard())
                else:
                    send_message(
                        vk,
                        user_id,
                        "Сначала выберите пост для удаления.",
                        get_main_keyboard(),
                    )

            elif text.startswith("🗑 пост #"):
                try:
                    match = re.search(r"#(\d+)", text)
                    if match:
                        post_id = int(match.group(1))
                        author = get_post_author(post_id)
                        if author == user_id:
                            post_link = f"https://vk.com/wall-{GROUP_ID}_{post_id}"
                            send_message(
                                vk,
                                user_id,
                                f"🔗 Ссылка на пост: {post_link}",
                            )
                            selected_post_for_delete[user_id] = post_id
                            send_message(
                                vk,
                                user_id,
                                f"⚠️ Удалить этот пост?",
                                get_confirm_keyboard(),
                            )
                        else:
                            send_message(vk, user_id, "❌ Это не ваш пост!", get_main_keyboard())
                    else:
                        send_message(vk, user_id, "Ошибка. Попробуйте снова.", get_main_keyboard())
                except Exception:
                    send_message(vk, user_id, "Ошибка. Попробуйте снова.", get_main_keyboard())

            else:
                send_message(vk, user_id, "Нажмите на кнопку в меню", get_main_keyboard())

# Запуск
if __name__ == "__main__":
    init_db()
    print("✅ База данных готова")

    publisher_thread = threading.Thread(target=run_publisher, daemon=True)
    publisher_thread.start()

    run_messenger()
