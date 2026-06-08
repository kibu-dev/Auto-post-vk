import os
import json
import time
import re
import traceback

import vk_api
from dotenv import load_dotenv

load_dotenv()

USER_TOKEN = os.getenv("USER_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

PUBLISHED_FILE = "published.json"


def load_published():
    try:
        with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"published": []}


def save_published(data):
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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
    # Чистим лишние пробелы
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = re.sub(r'\n\s*\n', '\n\n', cleaned)
    return cleaned


def make_profile_link(user_id, first_name, last_name):
    """
    Формирует ссылку на профиль ВК
    Пример: [id123456789|Иван Иванов]
    Или просто ссылку: https://vk.com/id123456789
    """
    # Способ 1: Внутренняя ссылка ВК (работает в постах)
    return f"[id{user_id}|{first_name} {last_name}]"
    
    # Альтернатива: обычная ссылка
    # return f"https://vk.com/id{user_id}"


vk_session = vk_api.VkApi(token=USER_TOKEN)
vk = vk_session.get_api()
published = load_published()

print("=" * 50)
print("🚀 БОТ ДЛЯ ПОДСЛУШАНО ЗАПУЩЕН")
print(f"📌 Группа: -{GROUP_ID}")
print(f"⏱ Интервал проверки: {CHECK_INTERVAL} сек.")
print("🔑 Ключевые слова анонимности: анон, анонимно, аноним, #анон, #анонимно, #аноним")
print("🔗 Подпись автора будет ссылкой на профиль ВК")
print("=" * 50 + "\n")

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
        
        for post in items:
            post_id = post["id"]
            from_id = post.get("from_id")
            
            # Пропускаем уже опубликованные
            if post_id in published["published"]:
                continue
            
            original_text = post.get("text", "")
            print(f"\n📝 Пост #{post_id}")
            print(f"👤 Автор (from_id): {from_id}")
            print(f"📄 Текст: {original_text[:80]}..." if original_text else "📄 Текст: (пусто)")
            
            # Проверяем ключевые слова анонимности
            is_anonymous = contains_anonymous_keyword(original_text)
            cleaned_text = remove_keywords(original_text)
            
            if is_anonymous:
                # Анонимный пост
                final_text = f"{cleaned_text}\n\n— Анонимно"
                print(f"🔒 АНОНИМНО (по ключевому слову)")
            else:
                # Пытаемся получить имя автора по from_id
                author_name = None
                if from_id and from_id > 0:
                    try:
                        user = vk.users.get(user_ids=from_id, fields="first_name,last_name")
                        if user and len(user) > 0:
                            first_name = user[0].get('first_name', '')
                            last_name = user[0].get('last_name', '')
                            author_name = f"{first_name} {last_name}".strip()
                            print(f"✅ Получено имя автора: {author_name}")
                    except Exception as e:
                        print(f"⚠️ Ошибка получения имени: {e}")
                
                if author_name and from_id:
                    # СОЗДАЕМ ССЫЛКУ на профиль ВК
                    profile_link = make_profile_link(from_id, first_name, last_name)
                    final_text = f"{cleaned_text}\n\n© {profile_link}"
                    print(f"✍️ ПОДПИСАНО со ссылкой: {profile_link}")
                else:
                    final_text = f"{cleaned_text}\n\n— Анонимно"
                    print(f"🔒 АНОНИМНО (имя не определено)")
            
            # Собираем вложения
            attachments = build_attachments(post)
            
            # Публикуем пост
            try:
                result = vk.wall.post(
                    owner_id=-GROUP_ID,
                    message=final_text,
                    attachments=attachments,
                    from_group=1
                )
                
                print(f"✅ Пост опубликован! ID: {result['post_id']}")
                
                # Удаляем предложку
                try:
                    vk.wall.delete(owner_id=from_id, post_id=post_id)
                    print(f"🗑 Предложка удалена")
                except Exception as e:
                    print(f"⚠️ Не удалось удалить предложку: {e}")
                
                published["published"].append(post_id)
                save_published(published)
                time.sleep(2)
                
            except vk_api.exceptions.ApiError as e:
                print(f"❌ Ошибка: {e}")
                if "[214]" in str(e):
                    print("Дубликат, пропускаем")
                    published["published"].append(post_id)
                    save_published(published)
        
        time.sleep(CHECK_INTERVAL)
        
    except Exception as e:
        print(f"⚠️ Ошибка: {e}")
        traceback.print_exc()
        time.sleep(60)
