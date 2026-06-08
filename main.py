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


def check_anonymous_keywords(text):
    """
    Проверяет наличие ключевых слов анонимности
    Возвращает (очищенный_текст, is_anonymous)
    """
    keywords = [
        "анон", "анонимно", "аноним",
        "#анон", "#анонимно", "#аноним"
    ]
    
    original_text = text
    is_anonymous = False
    
    for keyword in keywords:
        if re.search(r'\b' + re.escape(keyword) + r'\b', original_text, re.IGNORECASE):
            is_anonymous = True
            # Удаляем ключевое слово
            original_text = re.sub(r'\b' + re.escape(keyword) + r'\b', '', original_text, flags=re.IGNORECASE)
    
    # Чистим лишние пробелы
    cleaned_text = re.sub(r'\s+', ' ', original_text).strip()
    # Чистим лишние переносы
    cleaned_text = re.sub(r'\n\s*\n', '\n\n', cleaned_text)
    
    return cleaned_text, is_anonymous


vk_session = vk_api.VkApi(token=USER_TOKEN)
vk = vk_session.get_api()
published = load_published()

print("=" * 50)
print("🚀 БОТ ДЛЯ ПОДСЛУШАНО ЗАПУЩЕН")
print(f"📌 Группа: -{GROUP_ID}")
print(f"⏱ Интервал проверки: {CHECK_INTERVAL} сек.")
print("🔑 Ключевые слова анонимности: анон, анонимно, аноним, #анон, #анонимно, #аноним")
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
            from_id = post.get("from_id")  # ID автора предложки
            
            # Пропускаем уже опубликованные
            if post_id in published["published"]:
                continue
            
            print(f"\n📝 Обработка поста #{post_id}")
            print(f"👤 Автор предложки ID: {from_id}")
            
            original_text = post.get("text", "")
            print(f"📄 Текст: {original_text[:80]}...")
            
            # Проверяем ключевые слова анонимности
            cleaned_text, is_anonymous = check_anonymous_keywords(original_text)
            
            # Формируем финальный текст с подписью
            if is_anonymous:
                final_text = f"{cleaned_text}\n\n— Анонимно"
                print(f"🔒 ПОСТ БУДЕТ АНОНИМНЫМ")
            else:
                # Пытаемся получить имя автора
                author_name = None
                if from_id and from_id > 0:
                    try:
                        user = vk.users.get(user_ids=from_id, fields="first_name,last_name")
                        if user:
                            author_name = f"{user[0]['first_name']} {user[0]['last_name']}"
                    except Exception as e:
                        print(f"⚠️ Не удалось получить имя: {e}")
                
                if author_name:
                    final_text = f"{cleaned_text}\n\n© {author_name}"
                    print(f"✍️ ПОДПИСЬ АВТОРА: {author_name}")
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
                
                print(f"✅ Пост опубликован! ID записи: {result['post_id']}")
                
                # УДАЛЯЕМ ПРЕДЛОЖКУ из очереди
                try:
                    # Пробуем удалить через owner_id автора
                    vk.wall.delete(
                        owner_id=from_id,
                        post_id=post_id
                    )
                    print(f"🗑 Предложка #{post_id} удалена из очереди")
                except Exception as e:
                    # Если не получилось, пробуем удалить через группу
                    try:
                        vk.wall.delete(
                            owner_id=-GROUP_ID,
                            post_id=post_id
                        )
                        print(f"🗑 Предложка #{post_id} удалена (через группу)")
                    except:
                        print(f"⚠️ Не удалось удалить предложку, но пост опубликован")
                
                # Сохраняем в архив
                published["published"].append(post_id)
                save_published(published)
                
                time.sleep(2)  # пауза между постами
                
            except vk_api.exceptions.ApiError as e:
                print(f"❌ Ошибка публикации: {e}")
                if "[214]" in str(e):  # дубликат
                    print(f"Пост уже существует, пропускаем")
                    published["published"].append(post_id)
                    save_published(published)
        
        time.sleep(CHECK_INTERVAL)
        
    except Exception as e:
        print(f"⚠️ Ошибка: {e}")
        traceback.print_exc()
        time.sleep(60)
