import os
import json
import time
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
    """Собираем вложения (фото, видео и т.д.)"""
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

def get_final_text(post):
    """
    Формируем текст поста:
    - Если есть signer_id -> пользователь сам выбрал подпись, добавляем его имя
    - Если нет -> аноним
    """
    text = post.get("text", "")
    signer_id = post.get("signer_id")
    
    if signer_id and signer_id > 0:
        # Пользователь захотел подписаться — получаем имя
        try:
            user = vk.users.get(user_ids=signer_id, fields="first_name,last_name")
            if user:
                name = f"{user[0]['first_name']} {user[0]['last_name']}"
                return f"{text}\n\n— {name}"
        except Exception as e:
            print(f"Не удалось получить имя: {e}")
            return f"{text}\n\n— Пользователь"
    else:
        # Анонимный пост
        return f"{text}\n\n— Анонимно"

# Инициализация
vk_session = vk_api.VkApi(token=USER_TOKEN)
vk = vk_session.get_api()

published = load_published()

print("🚀 БОТ ЗАПУЩЕН")
print(f"📌 Группа: -{GROUP_ID}")
print(f"⏱ Интервал проверки: {CHECK_INTERVAL} сек.\n")

while True:
    try:
        # Получаем предложенные записи из сообщества
        response = vk.wall.get(
            owner_id=-GROUP_ID,
            filter="suggests",
            count=100
        )
        
        items = response.get("items", [])
        print(f"📨 Найдено предложенных постов: {len(items)}")
        
        for post in items:
            post_id = post["id"]
            
            # Пропускаем уже опубликованные
            if post_id in published["published"]:
                continue
            
            print(f"\n🆕 Новый пост #{post_id}")
            print(f"👤 signer_id: {post.get('signer_id', 'Нет (аноним)')}")
            
            # Формируем финальный текст
            final_text = get_final_text(post)
            attachments = build_attachments(post)
            
            try:
                # Публикуем
                result = vk.wall.post(
                    owner_id=-GROUP_ID,
                    from_group=1,
                    message=final_text,
                    attachments=attachments
                )
                
                print(f"✅ Опубликовано! ID записи: {result['post_id']}")
                
                # Сохраняем в архив
                published["published"].append(post_id)
                save_published(published)
                
                time.sleep(2)  # пауза между постами
                
            except vk_api.exceptions.ApiError as e:
                print(f"❌ Ошибка: {e}")
                if "[214]" in str(e):  # дубликат
                    print(f"Пост #{post_id} уже существует, пропускаем")
                    published["published"].append(post_id)
                    save_published(published)
        
        time.sleep(CHECK_INTERVAL)
        
    except Exception as e:
        print(f"⚠️ Ошибка: {e}")
        traceback.print_exc()
        time.sleep(60)
