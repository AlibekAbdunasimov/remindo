import sqlite3
import logging
from datetime import datetime

DB_PATH = 'notes.db'

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_notes_db():
    """Initialize the notes database with required tables"""
    with get_connection() as conn:
        c = conn.cursor()
        
        # Create notes table
        c.execute('''
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                topic_id INTEGER,
                message_id INTEGER NOT NULL,
                message_text TEXT NOT NULL,
                message_link TEXT NOT NULL,
                note_title TEXT,
                note_description TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')
        
        # Create note categories table
        c.execute('''
            CREATE TABLE IF NOT EXISTS note_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                category_name TEXT NOT NULL,
                color TEXT DEFAULT '#007AFF',
                created_at TEXT NOT NULL
            )
        ''')
        
        # Create note_category_mapping table
        c.execute('''
            CREATE TABLE IF NOT EXISTS note_category_mapping (
                note_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                FOREIGN KEY (note_id) REFERENCES notes (id),
                FOREIGN KEY (category_id) REFERENCES note_categories (id),
                PRIMARY KEY (note_id, category_id)
            )
        ''')
        
        # Add missing columns if they don't exist (for existing databases)
        try:
            c.execute('ALTER TABLE notes ADD COLUMN message_text TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            c.execute('ALTER TABLE notes ADD COLUMN message_link TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            c.execute('ALTER TABLE notes ADD COLUMN note_title TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            c.execute('ALTER TABLE notes ADD COLUMN note_description TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            c.execute('ALTER TABLE notes ADD COLUMN created_at TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            c.execute('ALTER TABLE notes ADD COLUMN updated_at TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            c.execute('ALTER TABLE notes ADD COLUMN topic_id INTEGER')
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            c.execute('ALTER TABLE notes ADD COLUMN message_id INTEGER')
        except sqlite3.OperationalError:
            pass  # Column already exists
        
        # Add indexes for better performance
        c.execute('CREATE INDEX IF NOT EXISTS idx_notes_user_chat ON notes (user_id, chat_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_notes_topic ON notes (topic_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_notes_created ON notes (created_at)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_categories_user_chat ON note_categories (user_id, chat_id)')
        
        conn.commit()

def add_note(user_id, chat_id, message_id, message_text, message_link, topic_id=None, title=None, description=None):
    """Add a new note to the database"""
    current_time = datetime.now().isoformat()
    
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO notes (user_id, chat_id, topic_id, message_id, message_text, message_link, note_title, note_description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, chat_id, topic_id, message_id, message_text, message_link, title, description, current_time, current_time))
        conn.commit()
        return c.lastrowid

def get_user_notes(user_id, chat_id, topic_id=None):
    """Get all notes for a user in a specific chat and topic"""
    logging.info(f"get_user_notes - User: {user_id}, Chat: {chat_id}, Topic: {topic_id}")
    with get_connection() as conn:
        c = conn.cursor()
        if topic_id is not None:
            # Get notes for specific topic
            logging.info(f"get_user_notes - Querying for specific topic: {topic_id}")
            c.execute('''
                SELECT id, message_text, message_link, note_title, note_description, created_at, topic_id
                FROM notes 
                WHERE user_id = ? AND chat_id = ? AND topic_id = ?
                ORDER BY created_at DESC
            ''', (user_id, chat_id, topic_id))
        else:
            # Get notes for general topic (topic_id is NULL)
            logging.info(f"get_user_notes - Querying for general topic (NULL)")
            c.execute('''
                SELECT id, message_text, message_link, note_title, note_description, created_at, topic_id
                FROM notes 
                WHERE user_id = ? AND chat_id = ? AND topic_id IS NULL
                ORDER BY created_at DESC
            ''', (user_id, chat_id))
        result = c.fetchall()
        logging.info(f"get_user_notes - Found {len(result)} notes")
        for note in result:
            logging.info(f"get_user_notes - Note {note[0]}: topic_id = {note[6]}")
        return result

def get_user_notes_forum_general(user_id, chat_id):
    """Get notes for general chat in forum groups.

    Treat topic_id NULL/None and also 0/1 as general topic for compatibility.
    """
    logging.info(f"get_user_notes_forum_general - User: {user_id}, Chat: {chat_id}")
    with get_connection() as conn:
        c = conn.cursor()
        
        # First, let's see ALL notes for this user and chat
        c.execute('''
            SELECT id, message_text, message_link, note_title, note_description, created_at, topic_id
            FROM notes 
            WHERE user_id = ? AND chat_id = ?
            ORDER BY created_at DESC
        ''', (user_id, chat_id))
        all_notes = c.fetchall()
        logging.info(f"get_user_notes_forum_general - Total notes for user/chat: {len(all_notes)}")
        for note in all_notes:
            logging.info(f"get_user_notes_forum_general - All notes - Note {note[0]}: topic_id = {note[6]}")
        
        # Now query for general chat notes (NULL, 0, 1 considered general)
        c.execute('''
            SELECT id, message_text, message_link, note_title, note_description, created_at, topic_id
            FROM notes 
            WHERE user_id = ? AND chat_id = ? AND (topic_id IS NULL OR topic_id IN (0,1))
            ORDER BY created_at DESC
        ''', (user_id, chat_id))
        result = c.fetchall()
        logging.info(f"get_user_notes_forum_general - Found {len(result)} general chat notes")
        for note in result:
            logging.info(f"get_user_notes_forum_general - General chat note {note[0]}: topic_id = {note[6]}")
        return result

def get_all_user_notes_in_chat(user_id, chat_id):
    """Get all notes for a user in a specific chat (all topics)"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT id, message_text, message_link, note_title, note_description, created_at, topic_id
            FROM notes 
            WHERE user_id = ? AND chat_id = ?
            ORDER BY created_at DESC
        ''', (user_id, chat_id))
        return c.fetchall()

def get_note_by_id(note_id, user_id):
    """Get a specific note by ID (user can only access their own notes)"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT id, user_id, chat_id, topic_id, message_id, message_text, message_link, note_title, note_description, created_at, updated_at
            FROM notes 
            WHERE id = ? AND user_id = ?
        ''', (note_id, user_id))
        return c.fetchone()

def delete_note(note_id, user_id):
    """Delete a note (user can only delete their own notes)"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            DELETE FROM notes 
            WHERE id = ? AND user_id = ?
        ''', (note_id, user_id))
        conn.commit()
        return c.rowcount > 0

def update_note(note_id, user_id, title=None, description=None):
    """Update note title or description"""
    current_time = datetime.now().isoformat()
    
    with get_connection() as conn:
        c = conn.cursor()
        
        if title is not None and description is not None:
            c.execute('''
                UPDATE notes 
                SET note_title = ?, note_description = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
            ''', (title, description, current_time, note_id, user_id))
        elif title is not None:
            c.execute('''
                UPDATE notes 
                SET note_title = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
            ''', (title, current_time, note_id, user_id))
        elif description is not None:
            c.execute('''
                UPDATE notes 
                SET note_description = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
            ''', (description, current_time, note_id, user_id))
        
        conn.commit()
        return c.rowcount > 0

def search_notes(user_id, chat_id, search_term, topic_id=None):
    """Search notes by text content, title, or description"""
    search_pattern = f'%{search_term}%'
    
    with get_connection() as conn:
        c = conn.cursor()
        if topic_id is not None:
            c.execute('''
                SELECT id, message_text, message_link, note_title, note_description, created_at, topic_id
                FROM notes 
                WHERE user_id = ? AND chat_id = ? AND topic_id = ? 
                AND (message_text LIKE ? OR note_title LIKE ? OR note_description LIKE ?)
                ORDER BY created_at DESC
            ''', (user_id, chat_id, topic_id, search_pattern, search_pattern, search_pattern))
        else:
            c.execute('''
                SELECT id, message_text, message_link, note_title, note_description, created_at, topic_id
                FROM notes 
                WHERE user_id = ? AND chat_id = ? AND topic_id IS NULL
                AND (message_text LIKE ? OR note_title LIKE ? OR note_description LIKE ?)
                ORDER BY created_at DESC
            ''', (user_id, chat_id, search_pattern, search_pattern, search_pattern))
        return c.fetchall()

def get_note_count(user_id, chat_id, topic_id=None):
    """Get the count of notes for a user in a specific chat/topic"""
    with get_connection() as conn:
        c = conn.cursor()
        if topic_id is not None:
            c.execute('''
                SELECT COUNT(*) FROM notes 
                WHERE user_id = ? AND chat_id = ? AND topic_id = ?
            ''', (user_id, chat_id, topic_id))
        else:
            c.execute('''
                SELECT COUNT(*) FROM notes 
                WHERE user_id = ? AND chat_id = ? AND topic_id IS NULL
            ''', (user_id, chat_id))
        return c.fetchone()[0]

def get_all_notes_in_chat(chat_id, topic_id=None):
    """Get all notes in a chat (for admin purposes)"""
    with get_connection() as conn:
        c = conn.cursor()
        if topic_id is not None:
            c.execute('''
                SELECT id, user_id, message_text, message_link, note_title, note_description, created_at, topic_id
                FROM notes 
                WHERE chat_id = ? AND topic_id = ?
                ORDER BY created_at DESC
            ''', (chat_id, topic_id))
        else:
            c.execute('''
                SELECT id, user_id, message_text, message_link, note_title, note_description, created_at, topic_id
                FROM notes 
                WHERE chat_id = ? AND topic_id IS NULL
                ORDER BY created_at DESC
            ''', (chat_id,))
        return c.fetchall()

def get_note_by_id_admin(note_id, chat_id):
    """Get a specific note by ID (for admin purposes)"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            SELECT id, user_id, chat_id, topic_id, message_id, message_text, message_link, note_title, note_description, created_at, updated_at
            FROM notes 
            WHERE id = ? AND chat_id = ?
        ''', (note_id, chat_id))
        return c.fetchone()

def admin_delete_note(note_id, chat_id):
    """Delete a note (for admin purposes)"""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            DELETE FROM notes 
            WHERE id = ? AND chat_id = ?
        ''', (note_id, chat_id))
        conn.commit()
        return c.rowcount > 0 