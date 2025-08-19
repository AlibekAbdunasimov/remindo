#!/usr/bin/env python3
"""
Migration script to convert SQLite data to PostgreSQL for Remindo Bot
"""

import sqlite3
import psycopg2
import os
import sys
from datetime import datetime
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def connect_sqlite(db_path):
    """Connect to SQLite database"""
    try:
        return sqlite3.connect(db_path)
    except sqlite3.Error as e:
        logger.error(f"SQLite connection error: {e}")
        return None

def connect_postgresql():
    """Connect to PostgreSQL database"""
    try:
        # Try to get DATABASE_URL from environment
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            # Build from individual components
            host = os.getenv('DB_HOST', 'localhost')
            port = os.getenv('DB_PORT', '5432')
            database = os.getenv('DB_NAME', 'remindo_bot')
            user = os.getenv('DB_USER', 'postgres')
            password = os.getenv('DB_PASSWORD', 'password')
            database_url = f"postgresql://{user}:{password}@{host}:{port}/{database}"
        
        return psycopg2.connect(database_url)
    except psycopg2.Error as e:
        logger.error(f"PostgreSQL connection error: {e}")
        return None

def migrate_reminders():
    """Migrate reminders from SQLite to PostgreSQL"""
    logger.info("Starting reminders migration...")
    
    # Connect to SQLite
    sqlite_conn = connect_sqlite('reminders.db')
    if not sqlite_conn:
        logger.error("Could not connect to SQLite reminders.db")
        return False
    
    # Connect to PostgreSQL
    pg_conn = connect_postgresql()
    if not pg_conn:
        logger.error("Could not connect to PostgreSQL")
        sqlite_conn.close()
        return False
    
    try:
        sqlite_cursor = sqlite_conn.cursor()
        pg_cursor = pg_conn.cursor()
        
        # Get all reminders from SQLite
        sqlite_cursor.execute("""
            SELECT user_id, chat_id, topic_id, message, remind_time, timezone, 
                   is_sent, is_recurring, recurrence_type, day_of_week, job_id
            FROM reminders
        """)
        reminders = sqlite_cursor.fetchall()
        
        logger.info(f"Found {len(reminders)} reminders to migrate")
        
        # Insert into PostgreSQL
        for reminder in reminders:
            user_id, chat_id, topic_id, message, remind_time, timezone, is_sent, is_recurring, recurrence_type, day_of_week, job_id = reminder
            
            # Convert SQLite boolean integers to PostgreSQL booleans
            is_sent = bool(is_sent)
            is_recurring = bool(is_recurring)
            
            pg_cursor.execute("""
                INSERT INTO reminders (user_id, chat_id, topic_id, message, remind_time, timezone, 
                                     is_sent, is_recurring, recurrence_type, day_of_week, job_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (user_id, chat_id, topic_id, message, remind_time, timezone, 
                  is_sent, is_recurring, recurrence_type, day_of_week, job_id))
        
        # Get timezone preferences from SQLite
        sqlite_cursor.execute("""
            SELECT entity_id, entity_type, timezone
            FROM timezone_preferences
        """)
        timezone_prefs = sqlite_cursor.fetchall()
        
        logger.info(f"Found {len(timezone_prefs)} timezone preferences to migrate")
        
        # Insert timezone preferences into PostgreSQL
        for pref in timezone_prefs:
            entity_id, entity_type, timezone = pref
            pg_cursor.execute("""
                INSERT INTO timezone_preferences (entity_id, entity_type, timezone)
                VALUES (%s, %s, %s)
                ON CONFLICT (entity_id, entity_type) 
                DO UPDATE SET timezone = EXCLUDED.timezone
            """, (entity_id, entity_type, timezone))
        
        pg_conn.commit()
        logger.info("Reminders migration completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error during reminders migration: {e}")
        pg_conn.rollback()
        return False
    finally:
        sqlite_conn.close()
        pg_conn.close()

def migrate_notes():
    """Migrate notes from SQLite to PostgreSQL"""
    logger.info("Starting notes migration...")
    
    # Connect to SQLite
    sqlite_conn = connect_sqlite('notes.db')
    if not sqlite_conn:
        logger.error("Could not connect to SQLite notes.db")
        return False
    
    # Connect to PostgreSQL
    pg_conn = connect_postgresql()
    if not pg_conn:
        logger.error("Could not connect to PostgreSQL")
        sqlite_conn.close()
        return False
    
    try:
        sqlite_cursor = sqlite_conn.cursor()
        pg_cursor = pg_conn.cursor()
        
        # Get all notes from SQLite
        sqlite_cursor.execute("""
            SELECT user_id, chat_id, topic_id, message_id, message_text, message_link, 
                   note_title, note_description, created_at, updated_at
            FROM notes
        """)
        notes = sqlite_cursor.fetchall()
        
        logger.info(f"Found {len(notes)} notes to migrate")
        
        # Insert into PostgreSQL
        for note in notes:
            user_id, chat_id, topic_id, message_id, message_text, message_link, note_title, note_description, created_at, updated_at = note
            
            # Convert ISO strings to timestamps if needed
            if isinstance(created_at, str):
                try:
                    created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                except:
                    created_at = None
            
            if isinstance(updated_at, str):
                try:
                    updated_at = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                except:
                    updated_at = None
            
            pg_cursor.execute("""
                INSERT INTO notes (user_id, chat_id, topic_id, message_id, message_text, message_link, 
                                 note_title, note_description, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (user_id, chat_id, topic_id, message_id, message_text, message_link, 
                  note_title, note_description, created_at, updated_at))
        
        pg_conn.commit()
        logger.info("Notes migration completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error during notes migration: {e}")
        pg_conn.rollback()
        return False
    finally:
        sqlite_conn.close()
        pg_conn.close()

def setup_postgresql_database():
    """Set up PostgreSQL database with required tables"""
    logger.info("Setting up PostgreSQL database...")
    
    try:
        # Import the database modules to initialize tables
        sys.path.append(os.path.join(os.path.dirname(__file__), 'Remindo_robot'))
        import db
        import notes_db
        
        # Initialize databases
        db.init_db()
        notes_db.init_notes_db()
        
        logger.info("PostgreSQL database setup completed")
        return True
        
    except Exception as e:
        logger.error(f"Error setting up PostgreSQL database: {e}")
        return False

def main():
    """Main migration function"""
    logger.info("Starting migration from SQLite to PostgreSQL")
    
    # Check if SQLite databases exist
    reminders_exists = os.path.exists('reminders.db')
    notes_exists = os.path.exists('notes.db')
    
    if not reminders_exists and not notes_exists:
        logger.info("No SQLite databases found. Setting up fresh PostgreSQL database...")
        if setup_postgresql_database():
            logger.info("Fresh PostgreSQL database setup completed")
        else:
            logger.error("Failed to set up PostgreSQL database")
            sys.exit(1)
        return
    
    # Set up PostgreSQL database first
    if not setup_postgresql_database():
        logger.error("Failed to set up PostgreSQL database")
        sys.exit(1)
    
    # Migrate data
    success = True
    
    if reminders_exists:
        if not migrate_reminders():
            success = False
    else:
        logger.info("No reminders.db found, skipping reminders migration")
    
    if notes_exists:
        if not migrate_notes():
            success = False
    else:
        logger.info("No notes.db found, skipping notes migration")
    
    if success:
        logger.info("Migration completed successfully!")
        logger.info("You can now delete the old SQLite files (reminders.db, notes.db) if desired")
    else:
        logger.error("Migration completed with errors")
        sys.exit(1)

if __name__ == "__main__":
    main()
