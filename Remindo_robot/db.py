import sqlite3
from datetime import datetime

DB_PATH = 'reminders.db'

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                topic_id INTEGER,
                message TEXT NOT NULL,
                remind_time TEXT NOT NULL,
                timezone TEXT NOT NULL,
                is_sent INTEGER DEFAULT 0,
                is_recurring INTEGER DEFAULT 0,
                recurrence_type TEXT,
                day_of_week TEXT,
                job_id TEXT
            )
        ''')
        
        # Create timezone preferences table
        c.execute('''
            CREATE TABLE IF NOT EXISTS timezone_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER NOT NULL,
                entity_type TEXT NOT NULL,
                timezone TEXT NOT NULL,
                UNIQUE(entity_id, entity_type)
            )
        ''')
        
        # Add new columns if they don't exist (for existing databases)
        try:
            c.execute('ALTER TABLE reminders ADD COLUMN is_recurring INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            c.execute('ALTER TABLE reminders ADD COLUMN recurrence_type TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            c.execute('ALTER TABLE reminders ADD COLUMN day_of_week TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            c.execute('ALTER TABLE reminders ADD COLUMN topic_id INTEGER')
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            c.execute('ALTER TABLE reminders ADD COLUMN job_id TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        conn.commit()

def add_reminder(user_id, chat_id, message, remind_time, timezone, is_recurring=0, recurrence_type=None, day_of_week=None, topic_id=None, job_id=None):
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO reminders (user_id, chat_id, topic_id, message, remind_time, timezone, is_sent, is_recurring, recurrence_type, day_of_week, job_id)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
        ''', (user_id, chat_id, topic_id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, job_id))
        conn.commit()
        return c.lastrowid

def get_pending_reminders():
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT id, user_id, chat_id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, topic_id, job_id FROM reminders WHERE is_sent = 0 OR is_recurring = 1
        ''')
        return c.fetchall()

def mark_reminder_sent(reminder_id):
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            UPDATE reminders SET is_sent = 1 WHERE id = ? AND is_recurring = 0
        ''', (reminder_id,))
        conn.commit()

def get_user_reminders(user_id, chat_id, topic_id=None):
    """Get all active reminders for a user in a specific chat and topic"""
    with get_connection() as conn:
        c = conn.cursor()
        if topic_id is not None:
            # Get reminders for specific topic
            c.execute('''
                SELECT id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, is_sent, topic_id
                FROM reminders 
                WHERE user_id = ? AND chat_id = ? AND topic_id = ? AND (is_sent = 0 OR is_recurring = 1)
                ORDER BY remind_time ASC
            ''', (user_id, chat_id, topic_id))
        else:
            # Get ALL reminders in the chat (for /list all command)
            c.execute('''
                SELECT id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, is_sent, topic_id
                FROM reminders 
                WHERE user_id = ? AND chat_id = ? AND (is_sent = 0 OR is_recurring = 1)
                ORDER BY remind_time ASC
            ''', (user_id, chat_id))
        return c.fetchall()

def get_user_general_topic_reminders(user_id, chat_id):
    """Get only reminders from general topic (topic_id IS NULL) for a user"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, is_sent, topic_id
            FROM reminders 
            WHERE user_id = ? AND chat_id = ? AND topic_id IS NULL AND (is_sent = 0 OR is_recurring = 1)
            ORDER BY remind_time ASC
        ''', (user_id, chat_id))
        return c.fetchall()

def get_reminder_by_id(reminder_id, user_id):
    """Get a specific reminder by ID, ensuring it belongs to the user"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, chat_id, topic_id
            FROM reminders 
            WHERE id = ? AND user_id = ?
        ''', (reminder_id, user_id))
        return c.fetchone()

def get_topic_name(chat_id, topic_id):
    """Get topic name from chat_id and topic_id (placeholder - would need bot API call)"""
    # This is a placeholder. In a real implementation, you'd need to make a bot API call
    # to get the topic name. For now, we'll return a generic name
    if topic_id is None:
        return "General"
    return f"Topic {topic_id}"

def delete_reminder(reminder_id, user_id):
    """Delete a reminder by ID, ensuring it belongs to the user"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            DELETE FROM reminders 
            WHERE id = ? AND user_id = ?
        ''', (reminder_id, user_id))
        conn.commit()
        return c.rowcount > 0

def update_reminder(reminder_id, user_id, message=None, remind_time=None, timezone=None, job_id=None, recurrence_type=None, day_of_week=None):
    """Update a reminder's fields"""
    with get_connection() as conn:
        c = conn.cursor()
        
        # Build dynamic update query
        updates = []
        params = []
        
        if message is not None:
            updates.append("message = ?")
            params.append(message)
        
        if remind_time is not None:
            updates.append("remind_time = ?")
            params.append(remind_time)
        
        if timezone is not None:
            updates.append("timezone = ?")
            params.append(timezone)
        
        if job_id is not None:
            updates.append("job_id = ?")
            params.append(job_id)
        
        if recurrence_type is not None:
            updates.append("recurrence_type = ?")
            params.append(recurrence_type)
        
        if day_of_week is not None:
            updates.append("day_of_week = ?")
            params.append(day_of_week)
        
        if not updates:
            return False
        
        query = f"UPDATE reminders SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
        params.extend([reminder_id, user_id])
        
        c.execute(query, params)
        conn.commit()
        return c.rowcount > 0

def get_reminder_job_id(reminder_id):
    """Get the job ID for a specific reminder"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT job_id FROM reminders WHERE id = ?', (reminder_id,))
        result = c.fetchone()
        return result[0] if result else None

def get_reminder_job_ids(reminder_id):
    """Get all job IDs for a specific reminder (handles comma-separated job IDs)"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT job_id FROM reminders WHERE id = ?', (reminder_id,))
        result = c.fetchone()
        if result and result[0]:
            # Split comma-separated job IDs
            return result[0].split(',')
        return []

def save_timezone_preference(entity_id, entity_type, timezone):
    """Save timezone preference for a user or chat"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO timezone_preferences (entity_id, entity_type, timezone)
            VALUES (?, ?, ?)
        ''', (entity_id, entity_type, timezone))
        conn.commit()
        return True

def get_timezone_preference(entity_id, entity_type):
    """Get timezone preference for a user or chat"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT timezone FROM timezone_preferences 
            WHERE entity_id = ? AND entity_type = ?
        ''', (entity_id, entity_type))
        result = c.fetchone()
        return result[0] if result else 'UTC'

def load_all_timezone_preferences():
    """Load all timezone preferences from database"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT entity_id, entity_type, timezone FROM timezone_preferences')
        return c.fetchall() 

def get_all_group_reminders(chat_id, topic_id=None):
    """Get all reminders in a group (admin function)"""
    with get_connection() as conn:
        c = conn.cursor()
        if topic_id is not None:
            # Get reminders for specific topic
            c.execute('''
                SELECT id, user_id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, topic_id
                FROM reminders 
                WHERE chat_id = ? AND topic_id = ? AND (is_sent = 0 OR is_recurring = 1)
                ORDER BY remind_time ASC
            ''', (chat_id, topic_id))
        else:
            # Get ALL reminders in the chat
            c.execute('''
                SELECT id, user_id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, topic_id
                FROM reminders 
                WHERE chat_id = ? AND (is_sent = 0 OR is_recurring = 1)
                ORDER BY remind_time ASC
            ''', (chat_id,))
        
        return c.fetchall()

def get_general_topic_reminders(chat_id):
    """Get only reminders from general topic (topic_id IS NULL)"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT id, user_id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, topic_id
            FROM reminders 
            WHERE chat_id = ? AND topic_id IS NULL AND (is_sent = 0 OR is_recurring = 1)
            ORDER BY remind_time ASC
        ''', (chat_id,))
        
        return c.fetchall()

def get_reminder_by_id_admin(reminder_id, chat_id):
    """Get a specific reminder by ID for admin (no user restriction)"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, chat_id, topic_id, user_id
            FROM reminders 
            WHERE id = ? AND chat_id = ?
        ''', (reminder_id, chat_id))
        return c.fetchone()

def admin_delete_reminder(reminder_id, chat_id):
    """Delete a reminder by ID for admin (no user restriction)"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            DELETE FROM reminders 
            WHERE id = ? AND chat_id = ?
        ''', (reminder_id, chat_id))
        conn.commit()
        return c.rowcount > 0

 