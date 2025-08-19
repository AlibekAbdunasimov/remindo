import psycopg2
import psycopg2.extras
from datetime import datetime
import logging
import config

def get_connection():
    """Get a PostgreSQL database connection"""
    try:
        conn = psycopg2.connect(config.DATABASE_URL)
        return conn
    except psycopg2.Error as e:
        logging.error(f"Database connection error: {e}")
        raise

def init_db():
    """Initialize PostgreSQL database with required tables"""
    with get_connection() as conn:
        with conn.cursor() as c:
            # Create reminders table
            c.execute('''
                CREATE TABLE IF NOT EXISTS reminders (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    chat_id BIGINT NOT NULL,
                    topic_id INTEGER,
                    message TEXT NOT NULL,
                    remind_time TIMESTAMP WITH TIME ZONE NOT NULL,
                    timezone TEXT NOT NULL,
                    is_sent BOOLEAN DEFAULT FALSE,
                    is_recurring BOOLEAN DEFAULT FALSE,
                    recurrence_type TEXT,
                    day_of_week TEXT,
                    job_id TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            ''')
            
            # Create timezone preferences table
            c.execute('''
                CREATE TABLE IF NOT EXISTS timezone_preferences (
                    id SERIAL PRIMARY KEY,
                    entity_id BIGINT NOT NULL,
                    entity_type TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    UNIQUE(entity_id, entity_type)
                )
            ''')
            
            # Add indexes for better performance
            c.execute('''
                CREATE INDEX IF NOT EXISTS idx_reminders_user_chat 
                ON reminders (user_id, chat_id)
            ''')
            c.execute('''
                CREATE INDEX IF NOT EXISTS idx_reminders_topic 
                ON reminders (topic_id)
            ''')
            c.execute('''
                CREATE INDEX IF NOT EXISTS idx_reminders_remind_time 
                ON reminders (remind_time)
            ''')
            c.execute('''
                CREATE INDEX IF NOT EXISTS idx_reminders_is_sent 
                ON reminders (is_sent)
            ''')
            c.execute('''
                CREATE INDEX IF NOT EXISTS idx_timezone_preferences_entity 
                ON timezone_preferences (entity_id, entity_type)
            ''')
            
            # Add migration logic for existing columns (PostgreSQL approach)
            try:
                # Check if columns exist and add them if they don't
                c.execute('''
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'reminders' AND column_name = 'created_at'
                ''')
                if not c.fetchone():
                    c.execute('ALTER TABLE reminders ADD COLUMN created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()')
                
                c.execute('''
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'reminders' AND column_name = 'updated_at'
                ''')
                if not c.fetchone():
                    c.execute('ALTER TABLE reminders ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()')
                    
            except psycopg2.Error as e:
                logging.warning(f"Error adding columns (might already exist): {e}")
            
            conn.commit()

def add_reminder(user_id, chat_id, message, remind_time, timezone, is_recurring=False, recurrence_type=None, day_of_week=None, topic_id=None, job_id=None):
    """Add a new reminder to the database"""
    with get_connection() as conn:
        with conn.cursor() as c:
            # For recurring reminders, we need to convert time string to a proper timestamp
            if is_recurring and isinstance(remind_time, str) and ':' in remind_time and len(remind_time) <= 8:
                # This looks like a time string (e.g., "19:19"), convert to today's date with that time
                from datetime import datetime, date
                import pytz
                
                try:
                    # Parse the time
                    if 'AM' in remind_time.upper() or 'PM' in remind_time.upper():
                        time_obj = datetime.strptime(remind_time.strip(), '%I:%M %p').time()
                    else:
                        time_obj = datetime.strptime(remind_time.strip(), '%H:%M').time()
                    
                    # Create a datetime for today with this time
                    today = date.today()
                    remind_datetime = datetime.combine(today, time_obj)
                    
                    # Convert to timezone-aware datetime
                    if timezone and timezone != 'UTC':
                        try:
                            tz = pytz.timezone(timezone)
                            remind_datetime = tz.localize(remind_datetime)
                        except:
                            # Fallback to UTC if timezone is invalid
                            remind_datetime = pytz.UTC.localize(remind_datetime)
                    else:
                        remind_datetime = pytz.UTC.localize(remind_datetime)
                    
                    remind_time = remind_datetime.isoformat()
                except Exception as e:
                    logging.error(f"Error converting time string {remind_time}: {e}")
                    # Fallback: use current time
                    remind_time = datetime.now(pytz.UTC).isoformat()
            
            c.execute('''
                INSERT INTO reminders (user_id, chat_id, topic_id, message, remind_time, timezone, is_sent, is_recurring, recurrence_type, day_of_week, job_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            ''', (user_id, chat_id, topic_id, message, remind_time, timezone, False, is_recurring, recurrence_type, day_of_week, job_id))
            reminder_id = c.fetchone()[0]
            conn.commit()
            return reminder_id

def get_pending_reminders():
    """Get all pending reminders (not sent or recurring)"""
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute('''
                SELECT id, user_id, chat_id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, topic_id, job_id 
                FROM reminders 
                WHERE is_sent = FALSE OR is_recurring = TRUE
                ORDER BY remind_time ASC
            ''')
            return c.fetchall()

def mark_reminder_sent(reminder_id):
    """Mark a one-time reminder as sent"""
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute('''
                UPDATE reminders 
                SET is_sent = TRUE, updated_at = NOW()
                WHERE id = %s AND is_recurring = FALSE
            ''', (reminder_id,))
            conn.commit()

def get_user_reminders(user_id, chat_id, topic_id=None):
    """Get all active reminders for a user in a specific chat and topic"""
    with get_connection() as conn:
        with conn.cursor() as c:
            if topic_id is not None:
                # Get reminders for specific topic
                c.execute('''
                    SELECT id, message, remind_time::text, timezone, is_recurring, recurrence_type, day_of_week, is_sent, topic_id
                    FROM reminders 
                    WHERE user_id = %s AND chat_id = %s AND topic_id = %s AND (is_sent = FALSE OR is_recurring = TRUE)
                    ORDER BY remind_time ASC
                ''', (user_id, chat_id, topic_id))
            else:
                # Get ALL reminders in the chat (for /list all command)
                c.execute('''
                    SELECT id, message, remind_time::text, timezone, is_recurring, recurrence_type, day_of_week, is_sent, topic_id
                    FROM reminders 
                    WHERE user_id = %s AND chat_id = %s AND (is_sent = FALSE OR is_recurring = TRUE)
                    ORDER BY remind_time ASC
                ''', (user_id, chat_id))
            return c.fetchall()

def get_user_general_topic_reminders(user_id, chat_id):
    """Get only reminders from general topic (topic_id IS NULL) for a user"""
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute('''
                SELECT id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, is_sent, topic_id
                FROM reminders 
                WHERE user_id = %s AND chat_id = %s AND topic_id IS NULL AND (is_sent = FALSE OR is_recurring = TRUE)
                ORDER BY remind_time ASC
            ''', (user_id, chat_id))
            return c.fetchall()

def get_reminder_by_id(reminder_id, user_id):
    """Get a specific reminder by ID, ensuring it belongs to the user"""
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute('''
                SELECT id, message, remind_time::text, timezone, is_recurring, recurrence_type, day_of_week, chat_id, topic_id
                FROM reminders 
                WHERE id = %s AND user_id = %s
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
        with conn.cursor() as c:
            c.execute('''
                DELETE FROM reminders 
                WHERE id = %s AND user_id = %s
            ''', (reminder_id, user_id))
            conn.commit()
            return c.rowcount > 0

def update_reminder(reminder_id, user_id, message=None, remind_time=None, timezone=None, job_id=None, recurrence_type=None, day_of_week=None):
    """Update a reminder's fields"""
    with get_connection() as conn:
        with conn.cursor() as c:
            
            # Build dynamic update query
            updates = []
            params = []
            
            if message is not None:
                updates.append("message = %s")
                params.append(message)
            
            if remind_time is not None:
                # Check if this is for a recurring reminder by getting current reminder info
                c.execute("SELECT is_recurring, timezone FROM reminders WHERE id = %s AND user_id = %s", (reminder_id, user_id))
                reminder_info = c.fetchone()
                
                if reminder_info and reminder_info[0]:  # is_recurring = True
                    # For recurring reminders, convert time string to proper timestamp
                    if isinstance(remind_time, str) and ':' in remind_time and len(remind_time) <= 8:
                        from datetime import datetime, date
                        import pytz
                        
                        try:
                            # Parse the time
                            if 'AM' in remind_time.upper() or 'PM' in remind_time.upper():
                                time_obj = datetime.strptime(remind_time.strip(), '%I:%M %p').time()
                            else:
                                time_obj = datetime.strptime(remind_time.strip(), '%H:%M').time()
                            
                            # Create a datetime for today with this time
                            today = date.today()
                            remind_datetime = datetime.combine(today, time_obj)
                            
                            # Convert to timezone-aware datetime
                            reminder_timezone = reminder_info[1] if reminder_info[1] else timezone
                            if reminder_timezone and reminder_timezone != 'UTC':
                                try:
                                    tz = pytz.timezone(reminder_timezone)
                                    remind_datetime = tz.localize(remind_datetime)
                                except:
                                    # Fallback to UTC if timezone is invalid
                                    remind_datetime = pytz.UTC.localize(remind_datetime)
                            else:
                                remind_datetime = pytz.UTC.localize(remind_datetime)
                            
                            remind_time = remind_datetime.isoformat()
                        except Exception as e:
                            logging.error(f"Error converting time string {remind_time} for recurring reminder: {e}")
                            # Keep original value if conversion fails
                
                updates.append("remind_time = %s")
                params.append(remind_time)
            
            if timezone is not None:
                updates.append("timezone = %s")
                params.append(timezone)
            
            if job_id is not None:
                updates.append("job_id = %s")
                params.append(job_id)
            
            if recurrence_type is not None:
                updates.append("recurrence_type = %s")
                params.append(recurrence_type)
            
            if day_of_week is not None:
                updates.append("day_of_week = %s")
                params.append(day_of_week)
            
            if not updates:
                return False
            
            # Add updated_at timestamp
            updates.append("updated_at = NOW()")
            
            query = f"UPDATE reminders SET {', '.join(updates)} WHERE id = %s AND user_id = %s"
            params.extend([reminder_id, user_id])
            
            c.execute(query, params)
            conn.commit()
            return c.rowcount > 0

def get_reminder_job_id(reminder_id):
    """Get the job ID for a specific reminder"""
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute('SELECT job_id FROM reminders WHERE id = %s', (reminder_id,))
            result = c.fetchone()
            return result[0] if result else None

def get_reminder_job_ids(reminder_id):
    """Get all job IDs for a specific reminder (handles comma-separated job IDs)"""
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute('SELECT job_id FROM reminders WHERE id = %s', (reminder_id,))
            result = c.fetchone()
            if result and result[0]:
                # Split comma-separated job IDs
                return result[0].split(',')
            return []

def save_timezone_preference(entity_id, entity_type, timezone):
    """Save timezone preference for a user or chat"""
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute('''
                INSERT INTO timezone_preferences (entity_id, entity_type, timezone)
                VALUES (%s, %s, %s)
                ON CONFLICT (entity_id, entity_type) 
                DO UPDATE SET timezone = EXCLUDED.timezone, created_at = NOW()
            ''', (entity_id, entity_type, timezone))
            conn.commit()
            return True

def get_timezone_preference(entity_id, entity_type):
    """Get timezone preference for a user or chat"""
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute('''
                SELECT timezone FROM timezone_preferences 
                WHERE entity_id = %s AND entity_type = %s
            ''', (entity_id, entity_type))
            result = c.fetchone()
            return result[0] if result else 'Asia/Tashkent'

def load_all_timezone_preferences():
    """Load all timezone preferences from database"""
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute('SELECT entity_id, entity_type, timezone FROM timezone_preferences')
            return c.fetchall() 

def get_all_group_reminders(chat_id, topic_id=None):
    """Get all reminders in a group (admin function)"""
    with get_connection() as conn:
        with conn.cursor() as c:
            if topic_id is not None:
                # Get reminders for specific topic
                c.execute('''
                    SELECT id, user_id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, topic_id
                    FROM reminders 
                    WHERE chat_id = %s AND topic_id = %s AND (is_sent = FALSE OR is_recurring = TRUE)
                    ORDER BY remind_time ASC
                ''', (chat_id, topic_id))
            else:
                # Get ALL reminders in the chat
                c.execute('''
                    SELECT id, user_id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, topic_id
                    FROM reminders 
                    WHERE chat_id = %s AND (is_sent = FALSE OR is_recurring = TRUE)
                    ORDER BY remind_time ASC
                ''', (chat_id,))
            
            return c.fetchall()

def get_general_topic_reminders(chat_id):
    """Get only reminders from general topic (topic_id IS NULL)"""
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute('''
                SELECT id, user_id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, topic_id
                FROM reminders 
                WHERE chat_id = %s AND topic_id IS NULL AND (is_sent = FALSE OR is_recurring = TRUE)
                ORDER BY remind_time ASC
            ''', (chat_id,))
            
            return c.fetchall()

def get_reminder_by_id_admin(reminder_id, chat_id):
    """Get a specific reminder by ID for admin (no user restriction)"""
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute('''
                SELECT id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, chat_id, topic_id, user_id
                FROM reminders 
                WHERE id = %s AND chat_id = %s
            ''', (reminder_id, chat_id))
            return c.fetchone()

def admin_delete_reminder(reminder_id, chat_id):
    """Delete a reminder by ID for admin (no user restriction)"""
    with get_connection() as conn:
        with conn.cursor() as c:
            c.execute('''
                DELETE FROM reminders 
                WHERE id = %s AND chat_id = %s
            ''', (reminder_id, chat_id))
            conn.commit()
            return c.rowcount > 0

 