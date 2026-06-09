import os
from dotenv import load_dotenv

load_dotenv()

# Токены
USER_TOKEN = os.getenv("USER_TOKEN")      # Токен пользователя/админа
GROUP_TOKEN = os.getenv("GROUP_TOKEN")    # Токен сообщества (для бота в ЛС)
GROUP_ID = int(os.getenv("GROUP_ID"))

# Настройки
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
PUBLISH_INTERVAL = int(os.getenv("PUBLISH_INTERVAL", "1800"))
BAN_HOURS = int(os.getenv("BAN_HOURS", "24"))

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

# ID администратора (кто получает уведомления в поддержку)
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # Твой ID ВК
