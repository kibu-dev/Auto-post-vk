import os
import json
import time
import re
import traceback
from datetime import datetime, timedelta

import vk_api
from dotenv import load_dotenv

# НОВОЕ: импорт базы данных
import database as db

load_dotenv()

USER_TOKEN = os.getenv("USER_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))  # Проверка новых постов (сек)
PUBLISH_INTERVAL = int(os.getenv("PUBLISH_INTERVAL", "1800"))  # Интервал между постами (сек) = 30 мин
BAN_HOURS = int(os.getenv("BAN_HOURS", "24"))  # Время бана в часах

PUBLISHED_FILE = "published.json"
BAN_FILE = "bans.json"  # Файл с временными банами

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
    """Загружает список временных банов"""
    try:
        with open(BAN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_bans(bans):
    """Сохраняет список банов"""
    with open(BAN_FILE, "w", encoding="utf-8") as f:
        json.dump(bans, f, ensure_ascii=False, indent=2)


def is_user_banned(user_id):
    """Проверяет, забанен ли пользователь и не истек ли срок бана"""
    bans = load_bans()
    
    if str(user_id) not in bans:
        return False
    
    ban_info = bans[str(user_id)]
    ban_until = datetime.fromisoformat(ban_info["until"])
    
    # Если срок бана истек - удаляем из бана
    if datetime.now() > ban_until:
        del bans[str(user_id)]
        save_bans(bans)
        print(f"✅ Срок бана пользователя {user_id} истек")
        return False
    
    # Если еще забанен
    remaining = ban_until - datetime.now()
    hours_left = int(remaining.total_seconds() // 3600)
    minutes_left = int((remaining.total_seconds() % 3600) // 60)
    print(f"🚫 Пользователь {user_id} в бане еще {hours_left}ч {minutes_left}м (причина: {ban_info['reason']})")
    return True


def ban_user(user_id, reason):
    """Банит пользователя на BAN_HOURS часов"""
    bans = load_bans()
    ban_until = datetime.now() + timedelta(hours=BAN_HOURS)
    
    bans[str(user_id)] = {
        "until": ban_until.isoformat(),
        "reason": reason,
        "banned_at": datetime.now().isoformat()
    }
    save_bans(bans)
    print(f"⚠️ Пользователь {user_id} забанен на {BAN_HOURS} часов. Причина: {reason}")


def build_attachments(post):
    """Собираем вложения"""
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
    """Проверка на запрещенные слова (реклама, услуги и т.д.)"""
    if not text:
        return False
    text_lower = text.lower()
    for word in FORBIDDEN_WORDS:
        if re.search(r'\b' + re.escape(word) + r'\b', text_lower):
            return True
    return False


def contains_forbidden_links(text):
    """Проверка на ссылки (реклама других ресурсов)"""
    if not text:
        return False
    text_lower = text.lower()
    for link in FORBIDDEN_LINKS:
        if link in text_lower:
            return True
    return False


def is_spam(text):
    """Комплексная проверка на спам"""
    if contains_forbidden_words(text):
        return True, "запрещенные слова (реклама/услуги)"
    if contains_forbidden_links(text):
        return True, "ссылки на сторонние ресурсы"
    return False, None


def delete_suggestion(vk, group_id, suggestion_id, from_id):
    """Удаляет предложенную запись"""
    try:
        vk.wall.delete(owner_id=-group_id, post_id=suggestion_id)
        print(f"   ✅ Предложка #{suggestion_id} удалена")
        return True
    except Exception as e:
        print(f"   ❌ Не удалось удалить предложку: {e}")
        return False


def get_user_full_name(vk, user_id):
    """Получает полное имя пользователя"""
    try:
        user = vk.users.get(user_ids=user_id, fields="first_name,last_name")
        if user and len(user) > 0:
            return user[0].get('first_name', ''), user[0].get('last_name', '')
    except:
        pass
    return "", ""


def contains_anonymous_keyword(text):
    """Проверяет, есть ли в тексте ключевое слово анонимности"""
    keywords = [
        "анон", "анонимно", "аноним",
        "#анон", "#анонимно", "#аноним"
    ]
    for keyword in keywords:
        if re.search(r'\b' + re.escape(keyword) + r'\b', text, re.IGNORECASE):
            return True
    return False


def remove_keywords(text):
    """Удаляет ключевые слова анонимности из текста"""
    keywords = [
        "анон", "анонимно", "аноним",
        "#анон", "#анонимно", "#аноним"
    ]
    cleaned = text
    for keyword in keywords:
        cleaned = re.sub(r'\b' + re.escape(keyword) + r'\b', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = re.sub(r'\n\s*\n', '\n\n', cleaned)
    return cleaned


def make_profile_link(user_id, first_name, last_name):
    """Формирует ссылку на профиль ВК"""
    return f"[id{user_id}|{first_name} {last_name}]"


def can_publish_now(last_publish_time, publish_interval):
    """Проверяет, можно ли публиковать сейчас (по таймеру)"""
    if last_publish_time is None:
        return True
    time_since_last = time.time() - last_publish_time
    return time_since_last >= publish_interval


# НОВОЕ: инициализируем базу данных при запуске
db.init_db()
print("✅ База данных инициализирована")

vk_session = vk_api.VkApi(token=USER_TOKEN)
vk = vk_session.get_api()
published = load_published()

# Таймер публикации
last_publish_time = None
publish_interval = PUBLISH_INTERVAL

print("=" * 60)
print("🚀 БОТ ДЛЯ ПОДСЛУШАНО ЗАПУЩЕН")
print(f"📌 Группа: -{GROUP_ID}")
print(f"⏱ Проверка новых постов: каждые {CHECK_INTERVAL} сек.")
print(f"⏰ Интервал между публикациями: {PUBLISH_INTERVAL // 60} мин.")
print(f"🚫 Время бана: {BAN_HOURS} часов")
print(f"🛡 Защита от спама: ВКЛЮЧЕНА")
print(f"🔗 Блокировка ссылок: ВКЛЮЧЕНА")
print("=" * 60 + "\n")

while True:
    try:
        # Получаем предложенные записи
        response = vk.wall.get(
            owner_id=-GROUP_ID,
            filter="suggests",
            count=100
        )
        
        items = response.get("items", [])
        print(f"📨 Найдено предложенных постов: {len(items)}")
        
        # Фильтруем посты
        pending_posts = []
        for post in items:
            post_id = post["id"]
            if post_id not in published["published"]:
                pending_posts.append(post)
        
        print(f"⏳ Ожидают публикации: {len(pending_posts)}")
        
        # Проверяем таймер и публикуем
        if pending_posts and can_publish_now(last_publish_time, publish_interval):
            post = pending_posts[0]  # Берем самый старый пост
            
            post_id = post["id"]
            from_id = post.get("from_id")
            original_text = post.get("text", "")
            
            print(f"\n📝 Обработка поста #{post_id}")
            print(f"👤 Автор (from_id): {from_id}")
            
            # 1. ПРОВЕРКА НА БАН
            if is_user_banned(from_id):
                print(f"🚫 ПОЛЬЗОВАТЕЛЬ В БАНЕ! Пост #{post_id} УДАЛЕН")
                delete_suggestion(vk, GROUP_ID, post_id, from_id)
                continue
            
            # 2. ПРОВЕРКА НА СПАМ
            is_spam_post, spam_reason = is_spam(original_text)
            if is_spam_post:
                print(f"🚫 ОБНАРУЖЕН СПАМ ({spam_reason})! Пост #{post_id} УДАЛЕН")
                print(f"   Текст: {original_text[:100]}...")
                delete_suggestion(vk, GROUP_ID, post_id, from_id)
                # Баним пользователя на 24 часа
                ban_user(from_id, spam_reason)
                continue
            
            # 3. АНОНИМНОСТЬ
            is_anonymous = contains_anonymous_keyword(original_text)
            cleaned_text = remove_keywords(original_text)
            
            # 4. ФОРМИРУЕМ ПОСТ
            if is_anonymous:
                final_text = f"{cleaned_text}\n\n— Анонимно"
                print(f"🔒 АНОНИМНО")
            else:
                first_name, last_name = get_user_full_name(vk, from_id)
                if first_name and last_name:
                    profile_link = make_profile_link(from_id, first_name, last_name)
                    final_text = f"{cleaned_text}\n\n© {profile_link}"
                    print(f"✍️ ПОДПИСАНО: {first_name} {last_name}")
                else:
                    final_text = f"{cleaned_text}\n\n— Анонимно"
                    print(f"🔒 АНОНИМНО (нет данных о пользователе)")
            
            # 5. ПУБЛИКУЕМ
            attachments = build_attachments(post)
            
            try:
                result = vk.wall.post(
                    owner_id=-GROUP_ID,
                    message=final_text,
                    attachments=attachments,
                    from_group=1
                )
                
                print(f"✅ Пост опубликован! ID: {result['post_id']}")
                last_publish_time = time.time()
                
                # Удаляем предложку
                delete_suggestion(vk, GROUP_ID, post_id, from_id)
                
                # Сохраняем в архив
                published["published"].append(post_id)
                save_published(published)
                
                # НОВОЕ: Сохраняем в базу данных для статистики и возможности удаления
                db.add_post(from_id, result['post_id'], cleaned_text)
                print(f"💾 Пост #{post_id} сохранен в БД")
                
                # Показываем время до следующей публикации
                next_publish_in = publish_interval
                print(f"⏰ Следующая публикация через {next_publish_in // 60} мин.\n")
                
            except vk_api.exceptions.ApiError as e:
                print(f"❌ Ошибка публикации: {e}")
                if "[214]" in str(e):
                    print("Дубликат, пропускаем")
                    published["published"].append(post_id)
                    save_published(published)
        else:
            if not pending_posts:
                print("📭 Нет постов для публикации")
            else:
                # Ждем до следующей публикации
                time_remaining = publish_interval - (time.time() - last_publish_time)
                if time_remaining > 0 and last_publish_time:
                    print(f"⏰ Следующая публикация через {int(time_remaining // 60)} мин.")
        
        time.sleep(CHECK_INTERVAL)
        
    except Exception as e:
        print(f"⚠️ Ошибка: {e}")
        traceback.print_exc()
        time.sleep(60)
