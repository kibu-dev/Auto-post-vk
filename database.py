import sqlite3
from datetime import datetime
from contextlib import contextmanager

DB_PATH = "posts.db"

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    """Создаем таблицы при первом запуске"""
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                posts_count INTEGER DEFAULT 0,
                last_post_date TEXT,
                total_chars INTEGER DEFAULT 0
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_posts (
                post_id INTEGER PRIMARY KEY,
                user_id INTEGER,
                published_date TEXT,
                text TEXT,
                is_deleted BOOLEAN DEFAULT 0
            )
        ''')
        
        conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_user_posts_user_id ON user_posts(user_id)
        ''')

def add_post(user_id, post_id, text):
    """Добавляем пост в статистику"""
    with get_db() as conn:
        # Добавляем пост
        conn.execute('''
            INSERT OR REPLACE INTO user_posts (post_id, user_id, published_date, text)
            VALUES (?, ?, ?, ?)
        ''', (post_id, user_id, datetime.now().isoformat(), text[:500]))
        
        # Обновляем статистику пользователя
        conn.execute('''
            INSERT INTO user_stats (user_id, posts_count, last_post_date, total_chars)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                posts_count = posts_count + 1,
                last_post_date = ?,
                total_chars = total_chars + ?
        ''', (user_id, datetime.now().isoformat(), len(text), datetime.now().isoformat(), len(text)))

def get_user_stats(user_id):
    """Получаем статистику пользователя"""
    with get_db() as conn:
        stats = conn.execute('SELECT * FROM user_stats WHERE user_id = ?', (user_id,)).fetchone()
        if stats:
            return dict(stats)
        return {"posts_count": 0, "last_post_date": None, "total_chars": 0}

def get_user_posts(user_id):
    """Получаем список постов пользователя"""
    with get_db() as conn:
        posts = conn.execute('''
            SELECT post_id, published_date, text, is_deleted 
            FROM user_posts 
            WHERE user_id = ? AND is_deleted = 0
            ORDER BY published_date DESC
        ''', (user_id,)).fetchall()
        return [dict(p) for p in posts]

def delete_user_post(user_id, post_id):
    """Отмечаем пост как удаленный"""
    with get_db() as conn:
        # Проверяем, что пост принадлежит пользователю
        post = conn.execute('SELECT * FROM user_posts WHERE post_id = ? AND user_id = ?', 
                           (post_id, user_id)).fetchone()
        if post:
            conn.execute('UPDATE user_posts SET is_deleted = 1 WHERE post_id = ?', (post_id,))
            # Уменьшаем счетчик постов
            conn.execute('UPDATE user_stats SET posts_count = posts_count - 1 WHERE user_id = ?', (user_id,))
            return True
        return False

def get_post_author(post_id):
    """Узнаем автора поста по его ID"""
    with get_db() as conn:
        post = conn.execute('SELECT user_id FROM user_posts WHERE post_id = ?', (post_id,)).fetchone()
        return post["user_id"] if post else None
