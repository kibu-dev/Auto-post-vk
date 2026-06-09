import time
import traceback
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from datetime import datetime, timedelta

import config
import database as db
import keyboards as kb
from database import get_user_stats, get_user_posts, delete_user_post, get_post_author

# Инициализация бота
vk_session = vk_api.VkApi(token=config.GROUP_TOKEN)
vk = vk_session.get_api()
longpoll = VkLongPoll(vk_session)

# Загружаем баны
def load_bans():
    try:
        with open("bans.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def check_group_subscription(user_id):
    """Проверяет, подписан ли пользователь на паблик"""
    try:
        response = vk.groups.isMember(group_id=config.GROUP_ID, user_id=user_id)
        return response
    except Exception as e:
        print(f"Ошибка проверки подписки: {e}")
        return False

def get_ban_info(user_id):
    """Получает информацию о бане пользователя"""
    bans = load_bans()
    if str(user_id) not in bans:
        return None
    
    ban_until = datetime.fromisoformat(bans[str(user_id)]["until"])
    if datetime.now() > ban_until:
        return None
    
    remaining = ban_until - datetime.now()
    hours_left = int(remaining.total_seconds() // 3600)
    minutes_left = int((remaining.total_seconds() % 3600) // 60)
    return {
        "hours": hours_left,
        "minutes": minutes_left,
        "reason": bans[str(user_id)]["reason"]
    }

def send_message(user_id, message, keyboard=None):
    """Отправляет сообщение пользователю"""
    try:
        vk.messages.send(
            user_id=user_id,
            message=message,
            random_id=0,
            keyboard=keyboard.get_keyboard() if keyboard else None
        )
    except Exception as e:
        print(f"Ошибка отправки сообщения: {e}")

def send_to_admin(message):
    """Отправляет сообщение админу"""
    if config.ADMIN_ID:
        try:
            vk.messages.send(
                user_id=config.ADMIN_ID,
                message=message,
                random_id=0
            )
        except Exception as e:
            print(f"Ошибка отправки админу: {e}")

def handle_start(user_id):
    """Обработка команды /start или Начать"""
    if not check_group_subscription(user_id):
        send_message(user_id, 
                    "❌ Вы не подписаны на наше сообщество!\n"
                    "Пожалуйста, подпишитесь, чтобы пользоваться ботом.\n"
                    f"https://vk.com/club{config.GROUP_ID}",
                    kb.get_back_keyboard())
        return
    
    ban_info = get_ban_info(user_id)
    if ban_info:
        send_message(user_id,
                    f"🚫 Вы забанены на {ban_info['hours']}ч {ban_info['minutes']}м\n"
                    f"Причина: {ban_info['reason']}\n\n"
                    f"Вы не можете управлять постами до окончания бана.",
                    kb.get_back_keyboard())
        return
    
    stats = get_user_stats(user_id)
    send_message(user_id,
                f"👋 Добро пожаловать!\n\n"
                f"📊 Ваша статистика:\n"
                f"• Опубликовано постов: {stats['posts_count']}\n"
                f"• Всего символов: {stats['total_chars']}\n"
                f"• Последний пост: {stats['last_post_date'][:16] if stats['last_post_date'] else 'Нет'}\n\n"
                f"Выберите действие:",
                kb.get_main_keyboard())

def handle_stats(user_id):
    """Показывает статистику пользователя"""
    stats = get_user_stats(user_id)
    ban_info = get_ban_info(user_id)
    
    message = f"📊 <b>Ваша статистика</b>\n\n"
    message += f"📝 Опубликовано постов: {stats['posts_count']}\n"
    message += f"🔤 Всего символов: {stats['total_chars']}\n"
    
    if stats['last_post_date']:
        message += f"📅 Последний пост: {stats['last_post_date'][:16]}\n"
    
    if ban_info:
        message += f"\n🚫 Бан истекает через: {ban_info['hours']}ч {ban_info['minutes']}м\n"
        message += f"Причина: {ban_info['reason']}"
    else:
        message += f"\n✅ Вы не в бане"
    
    send_message(user_id, message, kb.get_main_keyboard())

def handle_list_posts(user_id):
    """Показывает список постов пользователя для удаления"""
    posts = get_user_posts(user_id)
    
    if not posts:
        send_message(user_id, "📭 У вас нет опубликованных постов.", kb.get_main_keyboard())
        return
    
    send_message(user_id, 
                f"🗑 Выберите пост для удаления:\n"
                f"(всего постов: {len(posts)})",
                kb.get_posts_keyboard(posts))

def handle_delete_post(user_id, post_id):
    """Удаляет пост пользователя"""
    # Проверяем, что пост принадлежит пользователю
    author_id = get_post_author(post_id)
    
    if author_id != user_id:
        send_message(user_id, "❌ Этот пост не принадлежит вам!", kb.get_main_keyboard())
        return
    
    # Удаляем пост через API
    try:
        vk.wall.delete(owner_id=-config.GROUP_ID, post_id=post_id)
        # Отмечаем в БД
        delete_user_post(user_id, post_id)
        send_message(user_id, f"✅ Пост #{post_id} успешно удален!", kb.get_main_keyboard())
        
        # Логируем
        print(f"🗑 Пользователь {user_id} удалил пост #{post_id}")
        
    except Exception as e:
        send_message(user_id, f"❌ Ошибка при удалении: {e}", kb.get_main_keyboard())

def handle_support(user_id):
    """Отправляет сообщение в поддержку"""
    send_message(user_id, 
                "📝 Напишите ваше сообщение для администратора.\n"
                "Мы ответим вам в ближайшее время.\n\n"
                "Для отмены отправьте /cancel",
                kb.get_back_keyboard())
    
    # Ожидаем сообщение от пользователя
    return "waiting_for_support"

def send_support_message(user_id, message_text):
    """Отправляет сообщение админу"""
    user_info = vk.users.get(user_ids=user_id)[0]
    user_name = f"{user_info['first_name']} {user_info['last_name']}"
    
    admin_msg = f"📨 <b>Новое сообщение в поддержку</b>\n\n"
    admin_msg += f"👤 От: {user_name} (id{user_id})\n"
    admin_msg += f"💬 Сообщение:\n{message_text}"
    
    send_to_admin(admin_msg)
    send_message(user_id, "✅ Ваше сообщение отправлено администратору!", kb.get_main_keyboard())

def handle_help(user_id):
    """Помощь"""
    message = f"🤖 <b>Помощь по боту</b>\n\n"
    message += f"📊 <b>Моя статистика</b> - показывает количество ваших постов и активность\n\n"
    message += f"🗑 <b>Удалить мой пост</b> - вы можете удалить любой свой пост\n\n"
    message += f"🆘 <b>Написать в поддержку</b> - связаться с администратором\n\n"
    message += f"🔗 <b>Важно:</b> Для работы бота необходимо быть подписанным на паблик!\n\n"
    message += f"🚫 <b>За нарушение правил:</b> бан на {config.BAN_HOURS} часов"
    
    send_message(user_id, message, kb.get_main_keyboard())

# Словарь для ожидания сообщений в поддержку
waiting_for_support = {}

print("🤖 Бот для ЛС запущен...")
print(f"📌 Группа: club{config.GROUP_ID}")
print(f"👨‍💼 Админ: {config.ADMIN_ID}")
print("=" * 50)

# Основной цикл обработки сообщений
while True:
    try:
        for event in longpoll.listen():
            if event.type == VkEventType.MESSAGE_NEW and event.to_me:
                user_id = event.user_id
                message_text = event.text.lower().strip() if event.text else ""
                payload = event.payload
                
                # Проверяем, в режиме ли ожидания поддержки
                if user_id in waiting_for_support:
                    if message_text == "/cancel":
                        del waiting_for_support[user_id]
                        send_message(user_id, "❌ Отменено.", kb.get_main_keyboard())
                    else:
                        del waiting_for_support[user_id]
                        send_support_message(user_id, event.text)
                    continue
                
                # Обработка команд
                if message_text in ["начать", "/start", "меню", "start"]:
                    handle_start(user_id)
                
                elif message_text == "📊 моя статистика":
                    handle_stats(user_id)
                
                elif message_text == "🗑 удалить мой пост":
                    handle_list_posts(user_id)
                
                elif message_text == "🆘 написать в поддержку":
                    handle_support(user_id)
                
                elif message_text == "❓ помощь":
                    handle_help(user_id)
                
                elif message_text == "🔙 назад в меню" or (payload and payload.get("action") == "back"):
                    handle_start(user_id)
                
                elif payload and payload.get("action") == "confirm_delete":
                    post_id = payload.get("post_id")
                    handle_delete_post(user_id, post_id)
                
                elif payload and payload.get("post_id"):
                    # Запрос подтверждения удаления
                    post_id = payload.get("post_id")
                    send_message(user_id, 
                                f"⚠️ Вы уверены, что хотите удалить пост #{post_id}?\n"
                                f"Это действие необратимо.",
                                kb.get_confirm_keyboard(post_id))
                
                else:
                    send_message(user_id, 
                                "❌ Неизвестная команда. Нажмите кнопки меню.",
                                kb.get_main_keyboard())
    
    except Exception as e:
        print(f"Ошибка: {e}")
        traceback.print_exc()
        time.sleep(5)
