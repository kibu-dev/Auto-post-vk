from vk_api.keyboard import VkKeyboard, VkKeyboardColor

def get_main_keyboard():
    """Главное меню"""
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("📊 Моя статистика", color=VkKeyboardColor.PRIMARY)
    keyboard.add_line()
    keyboard.add_button("🗑 Удалить мой пост", color=VkKeyboardColor.NEGATIVE)
    keyboard.add_line()
    keyboard.add_button("🆘 Написать в поддержку", color=VkKeyboardColor.SECONDARY)
    keyboard.add_line()
    keyboard.add_button("❓ Помощь", color=VkKeyboardColor.SECONDARY)
    return keyboard

def get_posts_keyboard(posts):
    """Клавиатура с постами пользователя"""
    keyboard = VkKeyboard(one_time=True)
    
    # Показываем последние 10 постов (ВК ограничивает 40 кнопками)
    for i, post in enumerate(posts[:10], 1):
        post_preview = post['text'][:30] + "..." if len(post['text']) > 30 else post['text']
        keyboard.add_button(f"🗑 Пост #{post['post_id']} | {post_preview}", 
                           color=VkKeyboardColor.SECONDARY, 
                           payload={"post_id": post['post_id']})
        if i % 2 == 0 and i != len(posts[:10]):
            keyboard.add_line()
    
    keyboard.add_line()
    keyboard.add_button("🔙 Назад", color=VkKeyboardColor.PRIMARY, payload={"action": "back"})
    return keyboard

def get_back_keyboard():
    """Клавиатура с кнопкой назад"""
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("🔙 Назад в меню", color=VkKeyboardColor.PRIMARY, payload={"action": "back"})
    return keyboard

def get_confirm_keyboard(post_id):
    """Клавиатура подтверждения удаления"""
    keyboard = VkKeyboard(one_time=True)
    keyboard.add_button("✅ Да, удалить", color=VkKeyboardColor.NEGATIVE, payload={"action": "confirm_delete", "post_id": post_id})
    keyboard.add_button("❌ Нет, отмена", color=VkKeyboardColor.SECONDARY, payload={"action": "cancel"})
    return keyboard
