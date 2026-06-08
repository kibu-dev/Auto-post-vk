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

# Инициализация
vk_session = vk_api.VkApi(token=USER_TOKEN)
vk = vk_session.get_api()

published = load_published()

print("🚀 БОТ ЗАПУЩЕН")
print(f"📌 Группа: -{GROUP_ID}")
print(f"⏱ Интервал проверки: {CHECK_INTERVAL} сек.\n")

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
            signer_id = post.get("signer_id")  # Хочет ли подписаться (если есть - да)
            
            # Пропускаем уже опубликованные
            if post_id in published["published"]:
                continue
            
            print(f"\n🆕 Новый пост #{post_id}")
            print(f"📝 Автор предложки (from_id): {from_id}")
            print(f"✍️ Подпись (signer_id): {signer_id if signer_id else 'Нет (аноним)'}")
            
            # Формируем текст
            text = post.get("text", "")
            
            # Логика подписи
            if signer_id and signer_id > 0:
                # Пользователь хочет подписаться
                try:
                    user = vk.users.get(user_ids=signer_id, fields="first_name,last_name")
                    if user:
                        name = f"{user[0]['first_name']} {user[0]['last_name']}"
                        final_text = f"{text}\n\n— {name}"
                        print(f"✅ Пост с подписью: {name}")
                except Exception as e:
                    print(f"❌ Не удалось получить имя: {e}")
                    final_text = f"{text}\n\n— Пользователь"
            else:
                # Анонимный пост
                final_text = f"{text}\n\n— Анонимно"
                print(f"🔒 Анонимный пост")
            
            # Собираем вложения
            attachments = build_attachments(post)
            
            try:
                # Публикуем с указанием suggested_id (это важно!)
                result = vk.wall.post(
                    owner_id=-GROUP_ID,
                    from_group=1,
                    message=final_text,
                    attachments=attachments,
                    signed=1 if signer_id else 0,  # Указываем, нужна ли подпись
                    suggested_id=post_id  # КЛЮЧЕВОЙ ПАРАМЕТР - удаляет из предложок!
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
                elif "[100]" in str(e):
                    print(f"⚠️ Неверные параметры, возможно проблема с suggested_id")
        
        time.sleep(CHECK_INTERVAL)
        
    except Exception as e:
        print(f"⚠️ Ошибка: {e}")
        traceback.print_exc()
        time.sleep(60)
