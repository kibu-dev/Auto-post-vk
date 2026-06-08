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
    """Собираем вложения в формат VK API"""
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

def get_signer_name(vk, signer_id):
    """Получаем имя пользователя по ID для подписи"""
    if not signer_id or signer_id <= 0:
        return None
    try:
        user = vk.users.get(user_ids=signer_id, fields="first_name,last_name")
        if user:
            return f"{user[0]['first_name']} {user[0]['last_name']}"
    except Exception as e:
        print(f"Не удалось получить имя пользователя {signer_id}: {e}")
    return None

def publish_suggested_post(vk, post, group_id):
    """
    Публикует предложенный пост и удаляет его из очереди.
    Возвращает True если успешно, False если ошибка.
    """
    post_id = post["id"]
    from_id = post.get("from_id")
    signer_id = post.get("signer_id")  # Если есть - пользователь хочет подписаться
    text = post.get("text", "")
    
    print(f"  from_id: {from_id}")
    print(f"  signer_id: {signer_id if signer_id else 'Нет (аноним)'}")
    
    # Формируем финальный текст
    if signer_id and signer_id > 0:
        # Пользователь сам поставил галочку "Подписать пост"
        name = get_signer_name(vk, signer_id)
        if name:
            final_text = f"{text}\n\n— {name}"
            print(f"  ✅ Пост с ПОДПИСЬЮ: {name}")
        else:
            final_text = f"{text}\n\n— Пользователь"
            print(f"  ✅ Пост с ПОДПИСЬЮ (имя не получено)")
    else:
        # Пользователь выбрал анонимность
        final_text = f"{text}\n\n— Анонимно"
        print(f"  🔒 Анонимный пост")
    
    attachments = build_attachments(post)
    
    # КЛЮЧЕВОЙ МОМЕНТ: используем wall.post с параметром signed и без suggested_id
    # для сообществ suggested_id НЕ РАБОТАЕТ, вместо этого нужно просто опубликовать
    
    try:
        # Публикуем пост на стену сообщества
        result = vk.wall.post(
            owner_id=-group_id,
            from_group=1,
            message=final_text,
            attachments=attachments,
            signed=1 if signer_id else 0  # Указываем, нужна ли подпись
        )
        
        print(f"  Пост опубликован! ID: {result['post_id']}")
        
        # ТЕПЕРЬ УДАЛЯЕМ ПРЕДЛОЖКУ через wall.delete
        # У предложенных записей свой owner_id = from_id (автор предложки)
        try:
            vk.wall.delete(
                owner_id=from_id,
                post_id=post_id
            )
            print(f"  Предложка #{post_id} удалена из очереди")
        except Exception as e:
            print(f"  Не удалось удалить предложку: {e}")
            # Пробуем альтернативный способ - удаление от лица сообщества
            try:
                vk.wall.delete(
                    owner_id=-group_id,
                    post_id=post_id
                )
                print(f"  Предложка удалена через owner_id группы")
            except:
                print(f"  Предложка НЕ УДАЛЕНА, но опубликована")
        
        return True
        
    except vk_api.exceptions.ApiError as e:
        print(f"  Ошибка публикации: {e}")
        return False


# Инициализация
vk_session = vk_api.VkApi(token=USER_TOKEN)
vk = vk_session.get_api()

published = load_published()

print("=" * 50)
print("🚀 БОТ ДЛЯ ПОДСЛУШАНО ЗАПУЩЕН")
print(f"📌 Группа: -{GROUP_ID}")
print(f"⏱ Интервал проверки: {CHECK_INTERVAL} сек.")
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
            
            # Пропускаем уже опубликованные
            if post_id in published["published"]:
                continue
            
            print(f"\n📝 Обработка поста #{post_id}")
            
            # Публикуем
            success = publish_suggested_post(vk, post, GROUP_ID)
            
            if success:
                # Отмечаем как опубликованный
                published["published"].append(post_id)
                save_published(published)
                print(f"  ✅ Пост #{post_id} полностью обработан\n")
                time.sleep(2)  # пауза между постами
            else:
                print(f"  ❌ Ошибка при обработке поста #{post_id}\n")
        
        # Пауза до следующей проверки
        time.sleep(CHECK_INTERVAL)
        
    except vk_api.exceptions.ApiError as e:
        print(f"⚠️ Ошибка API: {e}")
        traceback.print_exc()
        time.sleep(60)
    except Exception as e:
        print(f"⚠️ Неизвестная ошибка: {e}")
        traceback.print_exc()
        time.sleep(60)
