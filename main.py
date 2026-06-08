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


vk_session = vk_api.VkApi(token=USER_TOKEN)
vk = vk_session.get_api()
published = load_published()

print("🚀 Бот запущен")
print(f"Группа: {GROUP_ID}")
print(f"Интервал: {CHECK_INTERVAL} сек\n")

while True:
    try:
        # Получаем предложки
        response = vk.wall.get(
            owner_id=-GROUP_ID,
            filter="suggests",
            count=100
        )
        
        items = response.get("items", [])
        print(f"📨 Найдено предложок: {len(items)}")
        
        for post in items:
            post_id = post["id"]
            
            if post_id in published["published"]:
                continue
            
            print(f"\n📝 Пост #{post_id}")
            
            # ТЕКСТ
            text = post.get("text", "")
            
            # АВТОР И ПОДПИСЬ
            from_id = post.get("from_id")  # кто отправил
            signer_id = post.get("signer_id")  # кто хочет подписаться (если None или 0 - аноним)
            
            print(f"from_id: {from_id}")
            print(f"signer_id: {signer_id}")
            
            # ЛОГИКА ПОДПИСИ
            final_text = text
            if signer_id and signer_id > 0:
                # Пользователь хочет подписаться - получаем имя
                try:
                    user = vk.users.get(user_ids=signer_id)
                    if user:
                        name = f"{user[0]['first_name']} {user[0]['last_name']}"
                        final_text = f"{text}\n\n© {name}"
                        print(f"✅ С ПОДПИСЬЮ: {name}")
                except:
                    final_text = f"{text}\n\n© Пользователь"
                    print(f"✅ С ПОДПИСЬЮ (пользователь)")
            else:
                # Анонимный пост
                final_text = f"{text}\n\n— Анонимно"
                print(f"🔒 АНОНИМНО")
            
            # ВЛОЖЕНИЯ
            attachments = build_attachments(post)
            
            # ПУБЛИКУЕМ И УДАЛЯЕМ ИЗ ПРЕДЛОЖЕК
            try:
                # ВАЖНО: используем suggested_id и правильный owner_id
                result = vk.wall.post(
                    owner_id=-GROUP_ID,
                    message=final_text,
                    attachments=attachments,
                    signed=0,  # отключаем автоматическую подпись ВК
                    suggested_id=post_id,  # ЭТО УДАЛЯЕТ ИЗ ПРЕДЛОЖЕК!
                    from_group=1
                )
                
                print(f"✅ Опубликовано! ID: {result['post_id']}")
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
