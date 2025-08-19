import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, Application, CallbackQueryHandler, MessageHandler, filters
from telegram.error import BadRequest
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from dateparser import parse as parse_date
from datetime import datetime, timedelta
import pytz
import asyncio
import db
import dateutil.parser
import re
from calendar import monthcalendar, month_name
import calendar
import notes_bot

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

import config

TOKEN = config.TOKEN  # Bot token from config file

# Retry configuration
REMINDER_MAX_RETRIES = config.REMINDER_MAX_RETRIES  # Maximum number of retry attempts
REMINDER_RETRY_DELAY_BASE = config.REMINDER_RETRY_DELAY_BASE  # Base delay for exponential backoff (2^attempt seconds)

scheduler = BackgroundScheduler(jobstores={
    'default': SQLAlchemyJobStore(url=config.DATABASE_URL)
})
scheduler.start()

db.init_db()

# Store timezones for both users and groups
user_timezones = {}  # for private chats: user_id -> tz
chat_timezones = {}  # for groups: chat_id -> tz

# Global app and loop references used by scheduled jobs (must not be passed as job args)
main_application = None
main_event_loop = None

def load_timezone_preferences():
    """Load timezone preferences from database into memory"""
    global user_timezones, chat_timezones
    preferences = db.load_all_timezone_preferences()
    for entity_id, entity_type, timezone in preferences:
        if entity_type == 'user':
            user_timezones[entity_id] = timezone
        elif entity_type == 'chat':
            chat_timezones[entity_id] = timezone

def get_user_timezone(user_id, chat_type, chat_id):
    """Resolve effective timezone with precedence: user override > chat > defaults.

    - If the user has explicitly set a personal timezone, use it everywhere (including groups)
    - Otherwise, in groups use the chat timezone if set (admin-configured)
    - Fall back to sensible defaults (user: Asia/Tashkent, chat: UTC)
    """
    # First check user-specific timezone (works in both private and group chats)
    user_tz = user_timezones.get(user_id)
    if user_tz is None:
        user_tz = db.get_timezone_preference(user_id, 'user')
        if user_tz != 'Asia/Tashkent':  # Cache only non-defaults
            user_timezones[user_id] = user_tz

    # If user has an explicit non-default timezone, prefer it
    if user_tz and user_tz != 'Asia/Tashkent':
        return user_tz

    # Otherwise, if in a group/supergroup, try chat timezone next
    if chat_type in ["group", "supergroup"]:
        chat_tz = chat_timezones.get(chat_id)
        if chat_tz is None:
            chat_tz = db.get_timezone_preference(chat_id, 'chat')
            if chat_tz != 'UTC':  # Cache only non-defaults
                chat_timezones[chat_id] = chat_tz
        return chat_tz or 'UTC'

    # Private chats fall back to the user's default
    return user_tz or 'Asia/Tashkent'

def get_topic_info_from_message(message):
    """Get topic information from a message object (for callback queries)"""
    chat = message.chat
    topic_id = None
    topic_name = ""
    
    # Check if this is a topic message
    if hasattr(message, 'message_thread_id') and message.message_thread_id:
        # In forum groups: message_thread_id = 1 is general chat, > 1 are topics
        # In regular groups: message_thread_id might exist but should be treated as general
        if chat.id < 0 and hasattr(chat, 'is_forum') and chat.is_forum:
            if message.message_thread_id == 1:
                # General chat in forum group
                topic_id = None
                topic_name = ""
            else:
                # Topic chat in forum group
                topic_id = message.message_thread_id
                topic_name = f"Topic #{topic_id}"
        else:
            # Regular group chat (not forum)
            topic_id = None
            topic_name = ""
    
    return topic_id, topic_name

async def get_topic_info(update, context=None):
    """Get topic information from the update"""
    chat = update.effective_chat
    topic_id = None
    topic_name = ""
    
    # Add detailed logging for reminder system
    logging.info(f"REMINDER get_topic_info - Chat ID: {chat.id}, Chat type: {chat.type}")
    logging.info(f"REMINDER get_topic_info - Has message_thread_id: {hasattr(update.message, 'message_thread_id')}")
    logging.info(f"REMINDER get_topic_info - Message thread ID: {getattr(update.message, 'message_thread_id', 'None')}")
    logging.info(f"REMINDER get_topic_info - Is forum: {getattr(chat, 'is_forum', 'Unknown')}")
    logging.info(f"REMINDER get_topic_info - Chat ID < 0: {chat.id < 0}")
    
    # Check if this is a topic message
    if hasattr(update.message, 'message_thread_id') and update.message.message_thread_id:
        # In forum groups: message_thread_id = 1 is general chat, > 1 are topics
        # In regular groups: message_thread_id might exist but should be treated as general
        if chat.id < 0 and hasattr(chat, 'is_forum') and chat.is_forum:
            logging.info(f"REMINDER get_topic_info - Forum group detected")
            if update.message.message_thread_id == 1 or update.message.message_thread_id is None:
                # General chat in forum group
                topic_id = None
                topic_name = ""
                logging.info(f"REMINDER get_topic_info - General chat in forum (message_thread_id = {update.message.message_thread_id})")
            else:
                # Topic chat in forum group
                topic_id = update.message.message_thread_id
                topic_name = f"Topic #{topic_id}"
                logging.info(f"REMINDER get_topic_info - Topic chat in forum (message_thread_id = {update.message.message_thread_id})")
        else:
            # Regular group chat (not forum)
            topic_id = None
            topic_name = ""
            logging.info(f"REMINDER get_topic_info - Regular group chat (not forum)")
    else:
        logging.info(f"REMINDER get_topic_info - No message_thread_id attribute or it's None")
    
    logging.info(f"REMINDER get_topic_info - Final topic_id: {topic_id}, topic_name: {topic_name}")
    return topic_id, topic_name

async def get_topic_info_from_callback(query):
    """Get topic information from callback query"""
    chat = query.message.chat
    topic_id = None
    topic_name = ""
    
    # Check if this is a topic message
    if hasattr(query.message, 'message_thread_id') and query.message.message_thread_id:
        # In forum groups: message_thread_id = 1 is general chat, > 1 are topics
        # In regular groups: message_thread_id might exist but should be treated as general
        if chat.id < 0 and hasattr(chat, 'is_forum') and chat.is_forum:
            if query.message.message_thread_id == 1 or query.message.message_thread_id is None:
                # General chat in forum group
                topic_id = None
                topic_name = ""
            else:
                # Topic chat in forum group
                topic_id = query.message.message_thread_id
                topic_name = f"Topic #{topic_id}"
        else:
            # Regular group chat (not forum)
            topic_id = None
            topic_name = ""
    
    return topic_id, topic_name

# Load timezone preferences on startup
load_timezone_preferences()

# Store user context for reminder creation and editing
user_reminder_context = {}  # user_id -> {date, time, message, step}
user_edit_context = {}  # user_id -> {reminder_id, field_to_edit}

main_event_loop = None  # Will be set in main

# Conversation states (not used anymore but kept for compatibility)
SELECTING_DATE, SELECTING_TIME, ENTERING_MESSAGE, EDITING_REMINDER = range(4)

# List of UTC offsets in order, as strings
UTC_OFFSETS = [
    '-12:00', '-11:00', '-10:00', '-09:30', '-09:00', '-08:00', '-07:00', '-06:00', '-05:00', '-04:30',
    '-04:00', '-03:30', '-03:00', '-02:00', '-01:00', '+00:00', '+01:00', '+02:00', '+03:00', '+03:30',
    '+04:00', '+04:30', '+05:00', '+05:30', '+05:45', '+06:00', '+06:30', '+07:00', '+08:00', '+08:45',
    '+09:00', '+09:30', '+10:00', '+10:30', '+11:00', '+12:00', '+12:45', '+13:00', '+14:00'
]

# Map UTC offsets to a representative IANA timezone (for scheduling)
UTC_OFFSET_TO_TZ = {
    '-12:00': 'Etc/GMT+12',
    '-11:00': 'Etc/GMT+11',
    '-10:00': 'Etc/GMT+10',
    '-09:30': 'Pacific/Marquesas',
    '-09:00': 'Etc/GMT+9',
    '-08:00': 'Etc/GMT+8',
    '-07:00': 'Etc/GMT+7',
    '-06:00': 'Etc/GMT+6',
    '-05:00': 'Etc/GMT+5',
    '-04:30': 'America/Caracas',
    '-04:00': 'Etc/GMT+4',
    '-03:30': 'America/St_Johns',
    '-03:00': 'Etc/GMT+3',
    '-02:00': 'Etc/GMT+2',
    '-01:00': 'Etc/GMT+1',
    '+00:00': 'Etc/GMT',
    '+01:00': 'Etc/GMT-1',
    '+02:00': 'Etc/GMT-2',
    '+03:00': 'Etc/GMT-3',
    '+03:30': 'Asia/Tehran',
    '+04:00': 'Etc/GMT-4',
    '+04:30': 'Asia/Kabul',
    '+05:00': 'Etc/GMT-5',
    '+05:30': 'Asia/Kolkata',
    '+05:45': 'Asia/Kathmandu',
    '+06:00': 'Etc/GMT-6',
    '+06:30': 'Asia/Yangon',
    '+07:00': 'Etc/GMT-7',
    '+08:00': 'Etc/GMT-8',
    '+08:45': 'Australia/Eucla',
    '+09:00': 'Etc/GMT-9',
    '+09:30': 'Australia/Darwin',
    '+10:00': 'Etc/GMT-10',
    '+10:30': 'Australia/Lord_Howe',
    '+11:00': 'Etc/GMT-11',
    '+12:00': 'Etc/GMT-12',
    '+12:45': 'Pacific/Chatham',
    '+13:00': 'Etc/GMT-13',
    '+14:00': 'Etc/GMT-14',
}

WEEKDAYS = [
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'
]

def parse_recurrence(text):
    # every day at HH:MM
    m = re.match(r'^every day at (\d{1,2}:\d{2})(?:\s|$)', text, re.IGNORECASE)
    if m:
        return {'type': 'daily', 'time': m.group(1)}
    # every week on <day> at HH:MM
    m = re.match(r'^every week on (\w+) at (\d{1,2}:\d{2})(?:\s|$)', text, re.IGNORECASE)
    if m and m.group(1).lower() in WEEKDAYS:
        return {'type': 'weekly', 'day': m.group(1).lower(), 'time': m.group(2)}
    return None

def create_calendar_keyboard(year, month):
    """Create calendar keyboard for the specified month"""
    keyboard = []
    
    # Month and year header
    keyboard.append([InlineKeyboardButton(f"{month_name[month]} {year}", callback_data="ignore")])
    
    # Days of week header
    days_of_week = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    keyboard.append([InlineKeyboardButton(day, callback_data="ignore") for day in days_of_week])
    
    # Calendar days
    cal = monthcalendar(year, month)
    today = datetime.now().date()
    
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="ignore"))
            else:
                date_obj = datetime(year, month, day).date()
                if date_obj < today:
                    # Past dates are disabled
                    row.append(InlineKeyboardButton(f"({day})", callback_data="ignore"))
                else:
                    # Future dates are clickable
                    date_str = date_obj.strftime("%Y-%m-%d")
                    row.append(InlineKeyboardButton(str(day), callback_data=f"select_date:{date_str}"))
        keyboard.append(row)
    
    # Navigation buttons
    nav_row = []
    if month > 1:
        prev_month = month - 1
        prev_year = year
    else:
        prev_month = 12
        prev_year = year - 1
    nav_row.append(InlineKeyboardButton("◀", callback_data=f"calendar:{prev_year}-{prev_month:02d}"))
    
    nav_row.append(InlineKeyboardButton("Today", callback_data=f"select_date:{today.strftime('%Y-%m-%d')}"))
    
    if month < 12:
        next_month = month + 1
        next_year = year
    else:
        next_month = 1
        next_year = year + 1
    nav_row.append(InlineKeyboardButton("▶", callback_data=f"calendar:{next_year}-{next_month:02d}"))
    
    keyboard.append(nav_row)
    
    # Add cancel button
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="one_time_cancel")])
    
    return InlineKeyboardMarkup(keyboard)

async def handle_topic_closed_error(update: Update, context: ContextTypes.DEFAULT_TYPE, error_message: str = None):
    """Handle Topic_closed error by sending a helpful message"""
    try:
        if error_message:
            await update.message.reply_text(
                f"❌ Topic Closed Error\n\n"
                f"{error_message}\n\n"
                "Solution:\n"
                "• Move to a different topic or the general chat\n"
                "• Or ask an admin to reopen the topic\n"
                "• You can still use bot commands in other topics"
            )
        else:
            await update.message.reply_text(
                "❌ Topic Closed Error\n\n"
                "This topic has been closed and the bot cannot send messages here.\n\n"
                "Solution:\n"
                "• Move to a different topic or the general chat\n"
                "• Or ask an admin to reopen the topic\n"
                "• You can still use bot commands in other topics"
            )
    except BadRequest as e:
        if "Topic_closed" in str(e):
            # Topic is closed, can't send any messages - just log it
            logging.info(f"Topic closed error handled silently - cannot send messages to closed topic")
        else:
            logging.error(f"BadRequest in topic closed error handler: {e}")
    except Exception as e:
        logging.error(f"Failed to send topic closed error message: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        topic_id, topic_name = await get_topic_info(update, context)
        topic_info = f" in {topic_name}" if topic_id else ""
        
        # Check if user has set a timezone
        chat = update.effective_chat
        user_id = update.effective_user.id
        current_tz = get_user_timezone(user_id, chat.type, chat.id)
        
        # Check if timezone is default (UTC)
        timezone_warning = ""
        if current_tz == "UTC":
            timezone_warning = "\n⚠️ Important: Please set your timezone using /settimezone before creating reminders to ensure accurate timing!\n"
        
        # Check if user is admin to show admin commands
        is_admin = await check_admin_permissions(update, context)
        
        start_text = f"Hello! I am your reminder bot{topic_info}. Here are the available commands:\n\n"
        start_text += "📝 Reminder Management:\n"
        start_text += "• /remind - Set a new reminder\n"
        start_text += "• /list - View reminders in current topic\n"
        start_text += "• /list all - View all reminders in this group\n"
        start_text += "• /delete <id> - Delete a specific reminder\n"
        start_text += "• /edit <id> - Edit an existing reminder\n\n"
        start_text += "📋 Note Taking (Supergroups Only):\n"
        start_text += "• /note - Save a message as a note (reply to message)\n"
        start_text += "• /notes - View notes in current topic\n"
        start_text += "• /notes all - View all notes in this group\n"
        start_text += "• /deletenote <id> - Delete a specific note\n"
        start_text += "• /editnote <id> - Edit a note's title\n\n"
        start_text += "⚙️ Settings:\n"
        start_text += "• /settimezone - Set timezone (admin only in groups)\n\n"
        
        if is_admin:
            start_text += "👑 Admin Commands:\n"
            start_text += "• /adminlist - View all reminders in group\n"
            start_text += "• /admindelete <id> - Delete any reminder in group\n\n"
        
        start_text += "Example: /remind 9:00 Take medicine\n\n"
        start_text += "💡 Topic Support: Reminders are automatically organized by topics in forum groups!"
        start_text += f"{timezone_warning}"
        
        await update.message.reply_text(start_text)
    except BadRequest as e:
        if "Topic_closed" in str(e):
            await handle_topic_closed_error(update, context)
        else:
            raise e

async def help_command(update, context):
    try:
        # Check if user is admin to show admin commands
        is_admin = await check_admin_permissions(update, context)
        
        help_text = """
🤖 Remindo Bot Help

📝 Reminder Management:
• /remind <time> <message> — Set a new reminder
  Example: /remind 9:00 Take medicine
• /list — View reminders in current topic
• /list all — View all reminders in this group
• /delete <id> — Delete a specific reminder
• /edit <id> — Edit an existing reminder

📋 Note Taking (Supergroups Only):
• /note — Save a message as a note (reply to message)
• /note <title> — Save with a title
• /note <text> — Create a new note
• /notes — View notes in current topic
• /notes all — View all notes in this group
• /deletenote <id> — Delete a specific note
• /editnote <id> — Edit a note's title

⚙️ Settings:
• /settimezone — Set timezone (admin only in groups)"""
        
        if is_admin:
            help_text += """

👑 Admin Commands:
• /adminlist — View all reminders in group
• /adminlist all — View all reminders from all topics
• /admindelete <id> — Delete any reminder in group"""
        
        help_text += """

For help or feedback, contact: @Type2Alibek_bot
        """
        
        await update.message.reply_text(help_text)
    except BadRequest as e:
        if "Topic_closed" in str(e):
            await handle_topic_closed_error(update, context)
        else:
            raise e

async def settimezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if this is an anonymous message (sent as group)
    if update.message.from_user.is_bot and update.message.from_user.username == 'GroupAnonymousBot':
        await update.message.reply_text(
            "❌ Anonymous commands are not supported for /settimezone\n\n"
            "Please send the /settimezone command as yourself (not as the group) to set your timezone.\n\n"
            "To disable 'Send as Group' for this bot:\n"
            "1. Go to group settings\n"
            "2. Find this bot in the admin list\n"
            "3. Disable 'Send as Group' option"
        )
        return
    
    chat = update.effective_chat
    user_id = update.message.from_user.id
    
    # Only allow timezone setting in private chats
    if chat.type != "private":
        await update.message.reply_text(
            "❌ Timezone setting is only available in private chat\n\n"
            "Please use /settimezone in private chat with the bot to set your timezone.\n\n"
            "To set your timezone:\n"
            "1. Start a private chat with @remindo_robot\n"
            "2. Send /settimezone\n"
            "3. Select your timezone"
        )
        return
    
    # Always set timezone for the user, not the group
    who = "user"
    current_tz = get_user_timezone(user_id, chat.type, chat.id)
    
    # Convert IANA timezone to UTC offset for display
    current_tz_display = current_tz
    for offset, tz_name in UTC_OFFSET_TO_TZ.items():
        if tz_name == current_tz:
            current_tz_display = offset
            break
    
    # Special handling for Asia/Tashkent (UTC+5)
    if current_tz == 'Asia/Tashkent':
        current_tz_display = '+05:00'
    
    # Table layout: 4 columns per row, only offset as label
    offset_keyboard = []
    row = []
    for i, offset in enumerate(UTC_OFFSETS):
        btn = InlineKeyboardButton(f"{offset}", callback_data=f"setoffset:{offset}")
        row.append(btn)
        if (i + 1) % 4 == 0:
            offset_keyboard.append(row)
            row = []
    if row:
        offset_keyboard.append(row)
    
    # Add cancel button at the bottom of the first column
    # Find the last row and add cancel button to the first position
    if offset_keyboard:
        # Add cancel button to the first position of the last row
        offset_keyboard[-1].insert(0, InlineKeyboardButton("❌ Cancel", callback_data="timezone_cancel"))
    else:
        # If no rows exist, create a new row with just the cancel button
        offset_keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="timezone_cancel")])
    
    reply_markup = InlineKeyboardMarkup(offset_keyboard)
    
    try:
        await update.message.reply_text(
            f"🌍 Your current timezone: {current_tz_display}\n\n"
            f"Please select your timezone (UTC offset):\n"
            f"If you don't know your offset, see: https://en.wikipedia.org/wiki/List_of_UTC_time_offsets\n"
            f"(This will set your personal timezone for all chats.)",
            reply_markup=reply_markup
        )
    except BadRequest as e:
        if "Topic_closed" in str(e):
            await handle_topic_closed_error(update, context)
        else:
            raise e



async def remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if this is an anonymous message (sent as group)
    if update.message.from_user.is_bot and update.message.from_user.username == 'GroupAnonymousBot':
        await update.message.reply_text(
            "❌ Anonymous commands are not supported for /remind\n\n"
            "Please send the /remind command as yourself (not as the group) to create reminders.\n\n"
            "To disable 'Send as Group' for this bot:\n"
            "1. Go to group settings\n"
            "2. Find this bot in the admin list\n"
            "3. Disable 'Send as Group' option"
        )
        return
    
    # Check if user has set a custom timezone
    user_id = update.message.from_user.id
    chat = update.effective_chat
    current_tz = get_user_timezone(user_id, chat.type, chat.id)
    
    # Check if user has explicitly set a timezone in database
    db_tz = db.get_timezone_preference(user_id, 'user')
    timezone_warning = ""
    if db_tz == 'Asia/Tashkent':  # User hasn't explicitly set a timezone (UTC+5)
        if chat.type == "private":
            timezone_warning = "\n\n⚠️ Timezone Notice: You're using the default timezone (UTC+5). To set your personal timezone, use /settimezone."
        else:
            timezone_warning = "\n\n⚠️ Timezone Notice: You're using the default timezone (UTC+5). To set your personal timezone, use /settimezone in private chat with @remindo_robot"
    
    if not context.args:
        # Show buttons for reminder types
        keyboard = [
            [InlineKeyboardButton("📅 One-time reminder", callback_data="remind_type:one_time")],
            [InlineKeyboardButton("🔄 Recurring reminder", callback_data="remind_type:recurring")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        try:
            await update.message.reply_text(
                f"Choose reminder type:{timezone_warning}",
                reply_markup=reply_markup
            )
        except BadRequest as e:
            if "Topic_closed" in str(e):
                await handle_topic_closed_error(update, context)
            else:
                raise e
        return
    
    # Handle text-based commands
    original_text = update.message.text
    command_and_rest = original_text.split(None, 1)
    if len(command_and_rest) < 2:
        try:
            await update.message.reply_text(f"Usage: /remind <time or recurrence> <message>{timezone_warning}")
        except BadRequest as e:
            if "Topic_closed" in str(e):
                await handle_topic_closed_error(update, context)
            else:
                raise e
        return
    rest = command_and_rest[1]
    recurrence = parse_recurrence(rest)
    chat = update.effective_chat
    topic_id, topic_name = await get_topic_info(update, context)
    tz_str = get_user_timezone(update.message.from_user.id, chat.type, chat.id)
    try:
        tz = pytz.timezone(tz_str)
    except Exception:
        tz = pytz.UTC
    if recurrence:
        # Recurring reminder
        if recurrence['type'] == 'daily':
            time_str = recurrence['time']
            message = rest.split(time_str, 1)[1].strip()
            hour, minute = map(int, time_str.split(':'))
            reminder_id = db.add_reminder(update.message.from_user.id, chat.id, message, time_str, tz_str, is_recurring=True, recurrence_type='daily', topic_id=topic_id)
            job = scheduler.add_job(
                schedule_reminder,
                'cron',
                hour=hour, minute=minute, timezone=tz,
                args=[chat.id, message, None, reminder_id, topic_id]
            )
            # Store the job ID
            db.update_reminder(reminder_id, update.message.from_user.id, job_id=job.id)
            topic_info = f" in {topic_name}" if topic_id else ""
            await update.message.reply_text(f"Daily recurring reminder set for {time_str} ({tz_str}){topic_info}! Message: {message}")
        elif recurrence['type'] == 'weekly':
            day = recurrence['day']
            time_str = recurrence['time']
            message = rest.split(time_str, 1)[1].strip()
            hour, minute = map(int, time_str.split(':'))
            reminder_id = db.add_reminder(update.message.from_user.id, chat.id, message, time_str, tz_str, is_recurring=True, recurrence_type='weekly', day_of_week=day, topic_id=topic_id)
            job = scheduler.add_job(
                schedule_reminder,
                'cron',
                day_of_week=day, hour=hour, minute=minute, timezone=tz,
                args=[chat.id, message, None, reminder_id, topic_id]
            )
            # Store the job ID
            db.update_reminder(reminder_id, update.message.from_user.id, job_id=job.id)
            topic_info = f" in {topic_name}" if topic_id else ""
            await update.message.reply_text(f"Weekly recurring reminder set for {day.title()} at {time_str} ({tz_str}){topic_info}! Message: {message}")
        return
    
    # One-time reminder (existing logic)
    command_and_time = original_text.split(None, 2)
    if len(command_and_time) < 3:
        await update.message.reply_text(f"Usage: /remind <time> <message>\nExample: /remind 9:00 Take medicine{timezone_warning}")
        return
    time_str = context.args[0]
    reminder_msg = command_and_time[2]
    now = datetime.now(tz)
    
    # Try to parse the time string
    reminder_time = parse_date(time_str, settings={'PREFER_DATES_FROM': 'future', 'TIMEZONE': tz_str, 'RETURN_AS_TIMEZONE_AWARE': True})
    
    if not reminder_time:
        await update.message.reply_text("Could not understand the time. Please try again.")
        logging.warning(f"User {update.message.from_user.id} provided invalid time string: {time_str}")
        return
    
    if reminder_time.tzinfo is None:
        reminder_time = tz.localize(reminder_time)
    
    # If the parsed time is in the past, try to interpret it as tomorrow
    if reminder_time < now:
        # Check if the input was just a time (HH:MM format) and if it's actually in the past
        if re.match(r'^\d{1,2}:\d{2}$', time_str):
            # Parse just the time part
            try:
                hour, minute = map(int, time_str.split(':'))
                
                # Check if this time has already passed today
                today_at_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if today_at_time.tzinfo is None:
                    today_at_time = tz.localize(today_at_time)
                
                if today_at_time < now:
                    # Time has passed today, set it for tomorrow
                    tomorrow = now + timedelta(days=1)
                    reminder_time = tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)
                    if reminder_time.tzinfo is None:
                        reminder_time = tz.localize(reminder_time)
                else:
                    # Time hasn't passed today, use today's time
                    reminder_time = today_at_time
                    
            except ValueError:
                await update.message.reply_text("Invalid time format. Please use HH:MM format.")
                return
        else:
            await update.message.reply_text("The time is in the past. Please try again.")
            logging.warning(f"User {update.message.from_user.id} tried to set reminder in the past: {reminder_time}")
            return
    chat_id = chat.id
    reminder_id = db.add_reminder(update.message.from_user.id, chat_id, reminder_msg, reminder_time.isoformat(), tz_str, topic_id=topic_id)
    try:
        job = scheduler.add_job(schedule_reminder, 'date', run_date=reminder_time, args=[chat_id, reminder_msg, reminder_time, reminder_id, topic_id])
        # Store the job ID
        db.update_reminder(reminder_id, update.message.from_user.id, job_id=job.id)
        topic_info = f" in {topic_name}" if topic_id else ""
        await update.message.reply_text(f"Reminder set for {reminder_time.strftime('%Y-%m-%d %H:%M:%S %Z')}{topic_info}! Message: {reminder_msg}")
        logging.info(f"Scheduled reminder for chat_id={chat_id} topic_id={topic_id} at {reminder_time} with message: {reminder_msg}")
    except Exception as e:
        await update.message.reply_text("Failed to schedule reminder. Please try again later.")
        logging.error(f"Failed to add job to scheduler for chat_id={chat_id}: {e}")
    return

async def transition_to_time_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Helper function to transition to time input state"""
    user_id = update.message.from_user.id
    if user_id in user_reminder_context:
        user_reminder_context[user_id]["step"] = "waiting_for_time"
        return SELECTING_TIME
    return

async def handle_reminder_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input for reminder creation and editing"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Check message length limit (Telegram message limit is 4096 characters)
    # We'll use 4000 to leave room for bot formatting and other text
    MAX_MESSAGE_LENGTH = 4000
    if len(text) > MAX_MESSAGE_LENGTH:
        await update.message.reply_text(
            f"❌ Message is too long! Maximum {MAX_MESSAGE_LENGTH} characters allowed.\n"
            f"Your message has {len(text)} characters.\n\n"
            "Please send a shorter message:"
        )
        return
    
    # Check if user is in note editing context first
    if user_id in notes_bot.user_note_context:
        user_context = notes_bot.user_note_context[user_id]
        step = user_context.get("step")
        
        if step == "editing_title":
            note_id = user_context["note_id"]
            new_title = update.message.text
            
            if notes_bot.notes_db.update_note(note_id, user_id, title=new_title):
                await update.message.reply_text(f"✅ Title updated successfully for Note #{note_id}!")
            else:
                await update.message.reply_text("❌ Failed to update title.")
            
            # Clear user context
            del notes_bot.user_note_context[user_id]
            return
    

    
    # Check if user is in edit context
    if user_id in user_edit_context:
        edit_context = user_edit_context[user_id]
        field_to_edit = edit_context.get("field_to_edit")
        step = edit_context.get("step")
        text = update.message.text
        if field_to_edit == "message":
            # Update the message
            reminder_id = edit_context["reminder_id"]
            
            # Get the old job IDs and remove them from scheduler
            old_job_ids = db.get_reminder_job_ids(reminder_id)
            for job_id in old_job_ids:
                try:
                    scheduler.remove_job(job_id)
                    logging.info(f"Removed old job {job_id} for reminder {reminder_id}")
                except Exception as e:
                    logging.warning(f"Failed to remove old job {job_id}: {e}")
            
            # Use regular update function
            success = db.update_reminder(reminder_id, user_id, message=text)
            
            if success:
                # Get the updated reminder details
                reminder_data = db.get_reminder_by_id(reminder_id, user_id)
                if reminder_data:
                    reminder_id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, chat_id, topic_id = reminder_data
                    
                    # Handle recurring reminders differently
                    if is_recurring:
                        # For recurring reminders, we need to reschedule the job with the new message
                        try:
                            tz = pytz.timezone(timezone)
                        except Exception:
                            tz = pytz.UTC
                        
                        # Parse time for recurring reminders
                        if isinstance(remind_time, str):
                            if ':' in remind_time and len(remind_time) <= 8:
                                # Time string format (e.g., "15:30")
                                hour, minute = map(int, remind_time.split(':'))
                            else:
                                # ISO timestamp string - parse it
                                reminder_datetime = dateutil.parser.isoparse(remind_time)
                                hour, minute = reminder_datetime.hour, reminder_datetime.minute
                        else:
                            # Already a datetime object
                            hour, minute = remind_time.hour, remind_time.minute
                        
                        if recurrence_type == 'daily':
                            # Daily recurring reminder
                            job = scheduler.add_job(
                                schedule_reminder,
                                'cron',
                                hour=hour, minute=minute, timezone=tz,
                                args=[chat_id, text, None, reminder_id, topic_id]
                            )
                            # Store the new job ID
                            db.update_reminder(reminder_id, user_id, job_id=job.id)
                        elif recurrence_type == 'weekly':
                            # Weekly recurring reminder
                            if day_of_week and ',' in day_of_week:
                                # Multiple weekdays - create multiple jobs
                                day_abbrevs = day_of_week.split(',')
                                job_ids = []
                                for day_abbrev in day_abbrevs:
                                    job = scheduler.add_job(
                                        schedule_reminder,
                                        'cron',
                                        day_of_week=day_abbrev, hour=hour, minute=minute, timezone=tz,
                                        args=[chat_id, text, None, reminder_id, topic_id]
                                    )
                                    job_ids.append(job.id)
                                # Store multiple job IDs
                                db.update_reminder(reminder_id, user_id, job_id=','.join(job_ids))
                            else:
                                # Single weekday
                                job = scheduler.add_job(
                                    schedule_reminder,
                                    'cron',
                                    day_of_week=day_of_week, hour=hour, minute=minute, timezone=tz,
                                    args=[chat_id, text, None, reminder_id, topic_id]
                                )
                                # Store the new job ID
                                db.update_reminder(reminder_id, user_id, job_id=job.id)
                        
                        await update.message.reply_text(f"✅ Recurring reminder {reminder_id} message updated to: {text}")
                    else:
                        # For one-time reminders, parse the ISO datetime and reschedule
                        try:
                            tz = pytz.timezone(timezone)
                        except Exception:
                            tz = pytz.UTC
                        
                        reminder_time = dateutil.parser.isoparse(remind_time)
                        if reminder_time.tzinfo is None:
                            reminder_time = tz.localize(reminder_time)
                        
                        # Add new scheduled job with updated message
                        job = scheduler.add_job(
                            schedule_reminder, 
                            'date', 
                            run_date=reminder_time, 
                            args=[chat_id, text, reminder_time, reminder_id, topic_id]
                        )
                        # Store the new job ID
                        db.update_reminder(reminder_id, user_id, job_id=job.id)
                        
                        await update.message.reply_text(f"✅ Reminder {reminder_id} message updated to: {text}")
                else:
                    await update.message.reply_text("❌ Failed to get reminder details.")
            else:
                await update.message.reply_text("❌ Failed to update reminder.")
            
            # Clear edit context
            del user_edit_context[user_id]
            return
        
        elif field_to_edit == "time":
            # Handle time input for editing
            step = edit_context.get("step")
            
            if step == "editing_recurring_time_input":
                # Handle recurring reminder time editing
                try:
                    parsed_time = parse_date(text, settings={'PREFER_DATES_FROM': 'future'})
                    if parsed_time:
                        time_str = parsed_time.strftime("%H:%M")
                        
                        # Get edit context
                        reminder_id = edit_context["reminder_id"]
                        selected_days = edit_context["selected_days"]
                        
                        # Get the old job IDs and remove them from scheduler
                        old_job_ids = db.get_reminder_job_ids(reminder_id)
                        for job_id in old_job_ids:
                            try:
                                scheduler.remove_job(job_id)
                                logging.info(f"Removed old job {job_id} for reminder {reminder_id}")
                            except Exception as e:
                                logging.warning(f"Failed to remove old job {job_id}: {e}")
                        
                        # Get timezone and topic information
                        chat = update.effective_chat
                        tz_str = get_user_timezone(user_id, chat.type, chat.id)
                        topic_id, topic_name = await get_topic_info(update, context)
                        
                        try:
                            tz = pytz.timezone(tz_str)
                        except Exception:
                            tz = pytz.UTC
                        
                        # Parse time
                        hour, minute = map(int, time_str.split(':'))
                        
                        # Map full day names to APScheduler format
                        day_mapping = {
                            'monday': 'mon',
                            'tuesday': 'tue', 
                            'wednesday': 'wed',
                            'thursday': 'thu',
                            'friday': 'fri',
                            'saturday': 'sat',
                            'sunday': 'sun'
                        }
                        
                        # Determine if it's daily or weekly
                        logging.info(f"Updating recurring reminder {reminder_id}: selected_days={selected_days}, time_str={time_str}")
                        logging.info(f"Number of selected days: {len(selected_days)}")
                        if len(selected_days) == 7:  # All days selected
                            # Update to daily recurring reminder
                            if db.update_reminder(reminder_id, user_id, remind_time=time_str, recurrence_type='daily', day_of_week=None):
                                # Create daily recurring job
                                job = scheduler.add_job(
                                    schedule_reminder,
                                    'cron',
                                    hour=hour, minute=minute, timezone=tz,
                                    args=[chat.id, edit_context["current_reminder"][1], None, reminder_id, topic_id]
                                )
                                # Store the new job ID
                                db.update_reminder(reminder_id, user_id, job_id=job.id)
                                
                                await update.message.reply_text(
                                    f"✅ Recurring reminder {reminder_id} updated to daily at {time_str}!"
                                )
                            else:
                                await update.message.reply_text("❌ Failed to update recurring reminder.")
                        else:
                            # Update to weekly recurring reminder with multiple days
                            # Store days as comma-separated string
                            day_abbrevs = [day_mapping.get(day, day) for day in selected_days]
                            days_string = ','.join(day_abbrevs)
                            
                            if db.update_reminder(reminder_id, user_id, remind_time=time_str, recurrence_type='weekly', day_of_week=days_string):
                                # Create multiple cron jobs for each day
                                job_ids = []
                                for day_abbrev in day_abbrevs:
                                    job = scheduler.add_job(
                                        schedule_reminder,
                                        'cron',
                                        day_of_week=day_abbrev, hour=hour, minute=minute, timezone=tz,
                                        args=[chat.id, edit_context["current_reminder"][1], None, reminder_id, topic_id]
                                    )
                                    job_ids.append(job.id)
                                
                                # Store multiple job IDs
                                db.update_reminder(reminder_id, user_id, job_id=','.join(job_ids))
                                
                                selected_text = ", ".join([day.title() for day in selected_days])
                                await update.message.reply_text(
                                    f"✅ Recurring reminder {reminder_id} updated for {selected_text} at {time_str}!"
                                )
                            else:
                                await update.message.reply_text("❌ Failed to update recurring reminder.")
                        
                        # Clear edit context
                        del user_edit_context[user_id]
                        return
                    else:
                        await update.message.reply_text("Invalid time format. Please use HH:MM or 2:30 PM format:")
                except Exception as e:
                    logging.error(f"Error parsing time: {e}")
                    await update.message.reply_text("Invalid time format. Please use HH:MM or 2:30 PM format:")
            elif step == "waiting_for_edit_time":
                # Handle one-time reminder time editing
                try:
                    parsed_time = parse_date(text, settings={'PREFER_DATES_FROM': 'future'})
                    if parsed_time:
                        time_str = parsed_time.strftime("%H:%M")
                        selected_date = edit_context["selected_date"]
                        
                        # Combine date and time
                        datetime_str = f"{selected_date} {time_str}"
                        
                        # Get timezone
                        chat = update.effective_chat
                        tz_str = get_user_timezone(user_id, chat.type, chat.id)
                        
                        try:
                            tz = pytz.timezone(tz_str)
                        except Exception:
                            tz = pytz.UTC
                        
                        # Parse the combined datetime
                        reminder_time = parse_date(datetime_str, settings={'PREFER_DATES_FROM': 'future', 'TIMEZONE': tz_str, 'RETURN_AS_TIMEZONE_AWARE': True})
                        
                        if not reminder_time:
                            await update.message.reply_text("Invalid date/time combination. Please try again.")
                            del user_edit_context[user_id]
                            return
                        
                        if reminder_time.tzinfo is None:
                            reminder_time = tz.localize(reminder_time)
                        
                        now = datetime.now(tz)
                        if reminder_time < now:
                            await update.message.reply_text("The time is in the past. Please try again.")
                            del user_edit_context[user_id]
                            return
                        
                        # Update the reminder time in database
                        reminder_id = edit_context["reminder_id"]
                        if db.update_reminder(reminder_id, user_id, remind_time=reminder_time.isoformat()):
                            # Remove old scheduled job and add new one
                            old_job_ids = db.get_reminder_job_ids(reminder_id)
                            for job_id in old_job_ids:
                                try:
                                    scheduler.remove_job(job_id)
                                    logging.info(f"Removed old job {job_id} for reminder {reminder_id}")
                                except Exception as e:
                                    logging.warning(f"Failed to remove old job {job_id}: {e}")
                            
                            # Add new scheduled job
                            job = scheduler.add_job(
                                schedule_reminder, 
                                'date', 
                                run_date=reminder_time, 
                                args=[chat.id, edit_context["current_reminder"][1], reminder_time, reminder_id]
                            )
                            # Store the new job ID
                            db.update_reminder(reminder_id, user_id, job_id=job.id)
                            
                            await update.message.reply_text(
                                f"✅ Reminder {reminder_id} time updated to {reminder_time.strftime('%Y-%m-%d %H:%M:%S %Z')}!"
                            )
                        else:
                            await update.message.reply_text("❌ Failed to update reminder.")
                        
                        # Clear edit context
                        del user_edit_context[user_id]
                        return
                    else:
                        await update.message.reply_text("Invalid time format. Please use HH:MM or 2:30 PM format:")
                except Exception as e:
                    logging.error(f"Error parsing time: {e}")
                    await update.message.reply_text("Invalid time format. Please use HH:MM or 2:30 PM format:")
        elif step == "editing_recurring_time_input":
            # Handle time input for editing recurring reminders
            try:
                parsed_time = parse_date(text, settings={'PREFER_DATES_FROM': 'future'})
                if parsed_time:
                    time_str = parsed_time.strftime("%H:%M")
                    
                    # Get edit context
                    reminder_id = edit_context["reminder_id"]
                    selected_days = edit_context["selected_days"]
                    
                    # Get the old job IDs and remove them from scheduler
                    old_job_ids = db.get_reminder_job_ids(reminder_id)
                    for job_id in old_job_ids:
                        try:
                            scheduler.remove_job(job_id)
                            logging.info(f"Removed old job {job_id} for reminder {reminder_id}")
                        except Exception as e:
                            logging.warning(f"Failed to remove old job {job_id}: {e}")
                    
                    # Get timezone and topic information
                    chat = update.effective_chat
                    tz_str = get_user_timezone(user_id, chat.type, chat.id)
                    topic_id, topic_name = await get_topic_info(update, context)
                    
                    try:
                        tz = pytz.timezone(tz_str)
                    except Exception:
                        tz = pytz.UTC
                    
                    # Parse time
                    hour, minute = map(int, time_str.split(':'))
                    
                    # Map full day names to APScheduler format
                    day_mapping = {
                        'monday': 'mon',
                        'tuesday': 'tue', 
                        'wednesday': 'wed',
                        'thursday': 'thu',
                        'friday': 'fri',
                        'saturday': 'sat',
                        'sunday': 'sun'
                    }
                    
                    # Determine if it's daily or weekly
                    logging.info(f"Updating recurring reminder {reminder_id}: selected_days={selected_days}, time_str={time_str}")
                    logging.info(f"Number of selected days: {len(selected_days)}")
                    if len(selected_days) == 7:  # All days selected
                        # Update to daily recurring reminder
                        if db.update_reminder(reminder_id, user_id, remind_time=time_str, recurrence_type='daily', day_of_week=None):
                            # Create daily recurring job
                            job = scheduler.add_job(
                                schedule_reminder,
                                'cron',
                                hour=hour, minute=minute, timezone=tz,
                                args=[chat.id, edit_context["current_reminder"][1], None, reminder_id, topic_id]
                            )
                            # Store the new job ID
                            db.update_reminder(reminder_id, user_id, job_id=job.id)
                            
                            await update.message.reply_text(
                                f"✅ Recurring reminder {reminder_id} updated to daily at {time_str}!"
                            )
                        else:
                            await update.message.reply_text("❌ Failed to update recurring reminder.")
                    else:
                        # Update to weekly recurring reminder with multiple days
                        # Store days as comma-separated string
                        day_abbrevs = [day_mapping.get(day, day) for day in selected_days]
                        days_string = ','.join(day_abbrevs)
                        
                        if db.update_reminder(reminder_id, user_id, remind_time=time_str, recurrence_type='weekly', day_of_week=days_string):
                            # Create multiple cron jobs for each day
                            job_ids = []
                            for day_abbrev in day_abbrevs:
                                job = scheduler.add_job(
                                    schedule_reminder,
                                    'cron',
                                    day_of_week=day_abbrev, hour=hour, minute=minute, timezone=tz,
                                    args=[chat.id, edit_context["current_reminder"][1], None, reminder_id, topic_id]
                                )
                                job_ids.append(job.id)
                            
                            # Store multiple job IDs
                            db.update_reminder(reminder_id, user_id, job_id=','.join(job_ids))
                            
                            selected_text = ", ".join([day.title() for day in selected_days])
                            await update.message.reply_text(
                                f"✅ Recurring reminder {reminder_id} updated for {selected_text} at {time_str}!"
                            )
                        else:
                            await update.message.reply_text("❌ Failed to update recurring reminder.")
                    
                    # Clear edit context
                    del user_edit_context[user_id]
                    return
                else:
                    await update.message.reply_text("Invalid time format. Please use HH:MM or 2:30 PM format:")
            except Exception as e:
                logging.error(f"Error parsing time: {e}")
                await update.message.reply_text("Invalid time format. Please use HH:MM or 2:30 PM format:")
                try:
                    parsed_time = parse_date(text, settings={'PREFER_DATES_FROM': 'future'})
                    if parsed_time:
                        time_str = parsed_time.strftime("%H:%M")
                        selected_date = edit_context["selected_date"]
                        
                        # Combine date and time
                        datetime_str = f"{selected_date} {time_str}"
                        
                        # Get timezone
                        chat = update.effective_chat
                        tz_str = get_user_timezone(user_id, chat.type, chat.id)
                        
                        try:
                            tz = pytz.timezone(tz_str)
                        except Exception:
                            tz = pytz.UTC
                        
                        # Parse the combined datetime
                        reminder_time = parse_date(datetime_str, settings={'PREFER_DATES_FROM': 'future', 'TIMEZONE': tz_str, 'RETURN_AS_TIMEZONE_AWARE': True})
                        
                        if not reminder_time:
                            await update.message.reply_text("Invalid date/time combination. Please try again.")
                            del user_edit_context[user_id]
                            return
                        
                        if reminder_time.tzinfo is None:
                            reminder_time = tz.localize(reminder_time)
                        
                        now = datetime.now(tz)
                        if reminder_time < now:
                            await update.message.reply_text("The time is in the past. Please try again.")
                            del user_edit_context[user_id]
                            return
                        
                        # Get topic information
                        topic_id, topic_name = await get_topic_info(update, context)
                        
                        # Update the reminder time in database
                        reminder_id = edit_context["reminder_id"]
                        
                        # Get the old job IDs and remove them from scheduler
                        old_job_ids = db.get_reminder_job_ids(reminder_id)
                        for job_id in old_job_ids:
                            try:
                                scheduler.remove_job(job_id)
                                logging.info(f"Removed old job {job_id} for reminder {reminder_id}")
                            except Exception as e:
                                logging.warning(f"Failed to remove old job {job_id}: {e}")
                        
                        # Get current reminder data to check if it's recurring
                        reminder_data = db.get_reminder_by_id(reminder_id, user_id)
                        if not reminder_data:
                            await update.message.reply_text("❌ Failed to get reminder details.")
                            del user_edit_context[user_id]
                            return
                        
                        _, _, _, _, is_recurring, recurrence_type, day_of_week, _, _ = reminder_data
                        
                        if is_recurring:
                            # Handle recurring reminder time update
                            # Extract time string (HH:MM) from the datetime
                            time_str = reminder_time.strftime("%H:%M")
                            
                            # Update the reminder time in database (store as time string for recurring)
                            if db.update_reminder(reminder_id, user_id, remind_time=time_str):
                                # Create new recurring job
                                try:
                                    tz = pytz.timezone(tz_str)
                                except Exception:
                                    tz = pytz.UTC
                                
                                hour, minute = map(int, time_str.split(':'))
                                
                                if recurrence_type == 'daily':
                                    # Daily recurring reminder
                                    job = scheduler.add_job(
                                        schedule_reminder,
                                        'cron',
                                        hour=hour, minute=minute, timezone=tz,
                                        args=[chat.id, edit_context["current_reminder"][1], None, reminder_id, topic_id]
                                    )
                                elif recurrence_type == 'weekly':
                                    # Weekly recurring reminder
                                    if ',' in day_of_week:
                                        # Multiple weekdays - create multiple jobs
                                        day_abbrevs = day_of_week.split(',')
                                        job_ids = []
                                        for day_abbrev in day_abbrevs:
                                            job = scheduler.add_job(
                                                schedule_reminder,
                                                'cron',
                                                day_of_week=day_abbrev, hour=hour, minute=minute, timezone=tz,
                                                args=[chat.id, edit_context["current_reminder"][1], None, reminder_id, topic_id]
                                            )
                                            job_ids.append(job.id)
                                        # Store multiple job IDs
                                        db.update_reminder(reminder_id, user_id, job_id=','.join(job_ids))
                                    else:
                                        # Single weekday
                                        job = scheduler.add_job(
                                            schedule_reminder,
                                            'cron',
                                            day_of_week=day_of_week, hour=hour, minute=minute, timezone=tz,
                                            args=[chat.id, edit_context["current_reminder"][1], None, reminder_id, topic_id]
                                        )
                                        # Store the new job ID
                                        db.update_reminder(reminder_id, user_id, job_id=job.id)
                                
                                await update.message.reply_text(
                                    f"✅ Recurring reminder {reminder_id} time updated to {time_str}!"
                                )
                            else:
                                await update.message.reply_text("❌ Failed to update recurring reminder.")
                        else:
                            # Handle one-time reminder time update
                            if db.update_reminder(reminder_id, user_id, remind_time=reminder_time.isoformat()):
                                # Add new scheduled job
                                job = scheduler.add_job(
                                    schedule_reminder, 
                                    'date', 
                                    run_date=reminder_time, 
                                    args=[chat.id, edit_context["current_reminder"][1], reminder_time, reminder_id, topic_id]
                                )
                                # Store the new job ID
                                db.update_reminder(reminder_id, user_id, job_id=job.id)
                                
                                await update.message.reply_text(
                                    f"✅ Reminder {reminder_id} time updated to {reminder_time.strftime('%Y-%m-%d %H:%M:%S %Z')}!"
                                )
                            else:
                                await update.message.reply_text("❌ Failed to update reminder.")
                        
                        # Clear edit context
                        del user_edit_context[user_id]
                        return
                    else:
                        await update.message.reply_text("Invalid time format. Please use HH:MM or 2:30 PM format:")
                except Exception as e:
                    logging.error(f"Error parsing time: {e}")
                    await update.message.reply_text("Invalid time format. Please use HH:MM or 2:30 PM format:")
        else:
            # Fallback: if we're in edit context but no specific handler matched
            logging.warning(f"No specific handler matched for edit context. field_to_edit={field_to_edit}, step={step}")
            logging.warning(f"Condition not matched. edit_context.get('step')='{edit_context.get('step')}', step='{step}'")
            logging.warning(f"edit_context.get('step') == 'editing_recurring_time_input': {edit_context.get('step') == 'editing_recurring_time_input'}")
            logging.warning(f"step == 'editing_recurring_time_input': {step == 'editing_recurring_time_input'}")
            logging.warning(f"step type: {type(step)}, step repr: {repr(step)}")
            logging.warning(f"edit_context.get('step') type: {type(edit_context.get('step'))}, edit_context.get('step') repr: {repr(edit_context.get('step'))}")
            await update.message.reply_text("I'm not sure what you want to edit. Please try again.")
            return
    
    # Handle regular reminder creation
    if user_id not in user_reminder_context:
        return
    
    step = user_reminder_context[user_id].get("step")
    text = update.message.text
    
    if step == "waiting_for_time":
        # Handle time input for one-time reminders
        try:
            # First validate the input format
            text_clean = text.strip().lower()
            
            # Check for common time formats
            if ':' in text and not any(word in text_clean for word in ['am', 'pm', 'a.m.', 'p.m.']):
                # HH:MM format (but not AM/PM format)
                parts = text.split(':')
                if len(parts) == 2:
                    try:
                        hour = int(parts[0])
                        minute = int(parts[1])
                        if 0 <= hour <= 23 and 0 <= minute <= 59:
                            time_str = f"{hour:02d}:{minute:02d}"
                            # Validate that the selected date+time is not in the past
                            date_str = user_reminder_context[user_id]['date']
                            chat = update.effective_chat
                            tz_str = get_user_timezone(user_id, chat.type, chat.id)
                            try:
                                tz = pytz.timezone(tz_str)
                            except Exception:
                                tz = pytz.UTC
                            try:
                                selected_date = datetime.strptime(date_str, "%Y-%m-%d")
                                candidate_dt = selected_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
                                if candidate_dt.tzinfo is None:
                                    candidate_dt = tz.localize(candidate_dt)
                                if candidate_dt < datetime.now(tz):
                                    await update.message.reply_text("The time is in the past. Please enter a future time:")
                                    return
                            except Exception as e:
                                logging.error(f"Error validating time against date: {e}")
                                await update.message.reply_text("Invalid time. Please try again with HH:MM or 2:30 PM format:")
                                return
                            user_reminder_context[user_id]["time"] = time_str
                            user_reminder_context[user_id]["step"] = "time_selected"
                            
                            await update.message.reply_text(
                                f"Date: {user_reminder_context[user_id]['date']}\n"
                                f"Time: {time_str}\n\n"
                                "Now send your reminder message:"
                            )
                            return
                    except ValueError:
                        pass
            elif any(word in text_clean for word in ['am', 'pm', 'a.m.', 'p.m.']):
                # Try to parse with dateparser for AM/PM format
                parsed_time = parse_date(text, settings={'PREFER_DATES_FROM': 'future'})
                if parsed_time:
                    time_str = parsed_time.strftime("%H:%M")
                    # Validate that the selected date+time is not in the past
                    date_str = user_reminder_context[user_id]['date']
                    hour, minute = map(int, time_str.split(':'))
                    chat = update.effective_chat
                    tz_str = get_user_timezone(user_id, chat.type, chat.id)
                    try:
                        tz = pytz.timezone(tz_str)
                    except Exception:
                        tz = pytz.UTC
                    try:
                        selected_date = datetime.strptime(date_str, "%Y-%m-%d")
                        candidate_dt = selected_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
                        if candidate_dt.tzinfo is None:
                            candidate_dt = tz.localize(candidate_dt)
                        if candidate_dt < datetime.now(tz):
                            await update.message.reply_text("The time is in the past. Please enter a future time:")
                            return
                    except Exception as e:
                        logging.error(f"Error validating time against date: {e}")
                        await update.message.reply_text("Invalid time. Please try again with HH:MM or 2:30 PM format:")
                        return
                    user_reminder_context[user_id]["time"] = time_str
                    user_reminder_context[user_id]["step"] = "time_selected"
                    
                    await update.message.reply_text(
                        f"Date: {user_reminder_context[user_id]['date']}\n"
                        f"Time: {time_str}\n\n"
                        "Now send your reminder message:"
                    )
                    return
                else:
                    await update.message.reply_text("Invalid time format. Please use HH:MM (e.g., 14:30) or 2:30 PM format:")
                    return
            
            # If we get here, the format is invalid
            await update.message.reply_text("Invalid time format. Please use HH:MM (e.g., 14:30) or 2:30 PM format:")
        except Exception as e:
            logging.error(f"Error parsing time: {e}")
            await update.message.reply_text("Invalid time format. Please use HH:MM or 2:30 PM format:")
    
    elif step == "recurring_time_input":
        # Handle time input for recurring reminders
        try:
            # First validate the input format
            text_clean = text.strip().lower()
            
            # Check for common time formats
            if ':' in text and not any(word in text_clean for word in ['am', 'pm', 'a.m.', 'p.m.']):
                # HH:MM format (but not AM/PM format)
                parts = text.split(':')
                if len(parts) == 2:
                    try:
                        hour = int(parts[0])
                        minute = int(parts[1])
                        if 0 <= hour <= 23 and 0 <= minute <= 59:
                            time_str = f"{hour:02d}:{minute:02d}"
                            user_reminder_context[user_id]["time"] = time_str
                            user_reminder_context[user_id]["step"] = "recurring_time_selected"
                            
                            selected_days = user_reminder_context[user_id]["selected_days"]
                            selected_text = ", ".join([day.title() for day in selected_days])
                            await update.message.reply_text(
                                f"Selected days: {selected_text}\n"
                                f"Time: {time_str}\n\n"
                                "Now send your reminder message (max 4000 characters):"
                            )
                            return
                    except ValueError:
                        pass
            elif any(word in text_clean for word in ['am', 'pm', 'a.m.', 'p.m.']):
                # Try to parse with dateparser for AM/PM format
                parsed_time = parse_date(text, settings={'PREFER_DATES_FROM': 'future'})
                if parsed_time:
                    time_str = parsed_time.strftime("%H:%M")
                    user_reminder_context[user_id]["time"] = time_str
                    user_reminder_context[user_id]["step"] = "recurring_time_selected"
                    
                    selected_days = user_reminder_context[user_id]["selected_days"]
                    selected_text = ", ".join([day.title() for day in selected_days])
                    await update.message.reply_text(
                        f"Selected days: {selected_text}\n"
                        f"Time: {time_str}\n\n"
                        "Now send your reminder message (max 4000 characters):"
                    )
                    return
                else:
                    await update.message.reply_text("Invalid time format. Please use HH:MM (e.g., 14:30) or 2:30 PM format:")
                    return
            
            # If we get here, the format is invalid
            await update.message.reply_text("Invalid time format. Please use HH:MM (e.g., 14:30) or 2:30 PM format:")
        except Exception as e:
            logging.error(f"Error parsing time: {e}")
            await update.message.reply_text("Invalid time format. Please use HH:MM or 2:30 PM format:")
    

    
    elif step == "time_selected":
        # Handle message input for one-time reminders
        message = text
        user_reminder_context[user_id]["message"] = message
        
        # Create the one-time reminder
        date_str = user_reminder_context[user_id]["date"]
        time_str = user_reminder_context[user_id]["time"]
        
        # Combine date and time
        datetime_str = f"{date_str} {time_str}"
        
        # Get timezone
        chat = update.effective_chat
        tz_str = get_user_timezone(user_id, chat.type, chat.id)
        
        try:
            tz = pytz.timezone(tz_str)
        except Exception:
            tz = pytz.UTC
        
        # Parse the combined datetime
        reminder_time = parse_date(datetime_str, settings={'PREFER_DATES_FROM': 'future', 'TIMEZONE': tz_str, 'RETURN_AS_TIMEZONE_AWARE': True})
        
        if not reminder_time:
            await update.message.reply_text("Invalid date/time combination. Please try again.")
            del user_reminder_context[user_id]
            return
        
        if reminder_time.tzinfo is None:
            reminder_time = tz.localize(reminder_time)
        
        now = datetime.now(tz)
        if reminder_time < now:
            await update.message.reply_text("The time is in the past. Please try again.")
            del user_reminder_context[user_id]
            return
        
        # Get topic information
        topic_id, topic_name = await get_topic_info(update, context)
        
        # Save to database and schedule
        reminder_id = db.add_reminder(user_id, chat.id, message, reminder_time.isoformat(), tz_str, topic_id=topic_id)
        job = scheduler.add_job(schedule_reminder, 'date', run_date=reminder_time, args=[chat.id, message, reminder_time, reminder_id, topic_id])
        # Store the job ID
        db.update_reminder(reminder_id, user_id, job_id=job.id)
        
        topic_info = f" in {topic_name}" if topic_id else ""
        # Truncate message for confirmation to avoid "Message is too long" error
        truncated_message = message[:200] + "..." if len(message) > 200 else message
        await update.message.reply_text(
            f"✅ Reminder set for {reminder_time.strftime('%Y-%m-%d %H:%M:%S %Z')}{topic_info}!\n"
            f"Message: {truncated_message}"
        )
        
        # Clear user context
        del user_reminder_context[user_id]
    
    elif step == "recurring_time_selected":
        # Handle message input for recurring reminders
        message = text
        user_reminder_context[user_id]["message"] = message
        
        # Get selected days and time
        selected_days = user_reminder_context[user_id]["selected_days"]
        time_str = user_reminder_context[user_id]["time"]
        
        # Get timezone
        chat = update.effective_chat
        tz_str = get_user_timezone(user_id, chat.type, chat.id)
        
        try:
            tz = pytz.timezone(tz_str)
        except Exception:
            tz = pytz.UTC
        
        # Parse time
        hour, minute = map(int, time_str.split(':'))
        
        # Map full day names to APScheduler format
        day_mapping = {
            'monday': 'mon',
            'tuesday': 'tue', 
            'wednesday': 'wed',
            'thursday': 'thu',
            'friday': 'fri',
            'saturday': 'sat',
            'sunday': 'sun'
        }
        
        # Get topic information
        topic_id, topic_name = await get_topic_info(update, context)
        
        # Determine if it's daily or weekly
        if len(selected_days) == 7:  # All days selected
            # Create daily recurring reminder
            reminder_id = db.add_reminder(user_id, chat.id, message, time_str, tz_str, is_recurring=True, recurrence_type='daily', topic_id=topic_id)
            job = scheduler.add_job(
                schedule_reminder,
                'cron',
                hour=hour, minute=minute, timezone=tz,
                args=[chat.id, message, None, reminder_id, topic_id]
            )
            # Store the job ID
            db.update_reminder(reminder_id, user_id, job_id=job.id)
            
            selected_text = "Every day"
        else:
            # Create weekly recurring reminder with multiple days
            # Store days as comma-separated string
            day_abbrevs = [day_mapping.get(day, day) for day in selected_days]
            days_string = ','.join(day_abbrevs)
            
            reminder_id = db.add_reminder(user_id, chat.id, message, time_str, tz_str, is_recurring=True, recurrence_type='weekly', day_of_week=days_string, topic_id=topic_id)
            
            # Create multiple cron jobs for each day
            job_ids = []
            for day_abbrev in day_abbrevs:
                job = scheduler.add_job(
                    schedule_reminder,
                    'cron',
                    day_of_week=day_abbrev, hour=hour, minute=minute, timezone=tz,
                    args=[chat.id, message, None, reminder_id, topic_id]
                )
                job_ids.append(job.id)
            
            # Store the first job ID (we'll need to modify the database to store multiple job IDs)
            db.update_reminder(reminder_id, user_id, job_id=','.join(job_ids))
            
            selected_text = ", ".join([day.title() for day in selected_days])
        
        topic_info = f" in {topic_name}" if topic_id else ""
        # Truncate message for confirmation to avoid "Message is too long" error
        truncated_message = message[:200] + "..." if len(message) > 200 else message
        await update.message.reply_text(
            f"✅ Recurring reminder set for {selected_text} at {time_str} ({tz_str}){topic_info}!\n"
            f"Message: {truncated_message}"
        )
        
        # Clear user context
        del user_reminder_context[user_id]

async def reminder_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    try:
        await query.answer()
    except Exception as e:
        logging.warning(f"Failed to answer callback query: {e}")
        # Continue processing even if answer fails
    
    if query.data.startswith("remind_type:"):
        remind_type = query.data.split(":", 1)[1]
        if remind_type == "one_time":
            # Show calendar for date selection
            today = datetime.now()
            keyboard = create_calendar_keyboard(today.year, today.month)
            await query.edit_message_text(
                "Select a date for your reminder:",
                reply_markup=keyboard
            )
        elif remind_type == "recurring":
            # Show weekday selection for recurring reminders
            keyboard = [
                [InlineKeyboardButton("Every day", callback_data="select_all_days")],
                [InlineKeyboardButton("Monday", callback_data="toggle_day:monday")],
                [InlineKeyboardButton("Tuesday", callback_data="toggle_day:tuesday")],
                [InlineKeyboardButton("Wednesday", callback_data="toggle_day:wednesday")],
                [InlineKeyboardButton("Thursday", callback_data="toggle_day:thursday")],
                [InlineKeyboardButton("Friday", callback_data="toggle_day:friday")],
                [InlineKeyboardButton("Saturday", callback_data="toggle_day:saturday")],
                [InlineKeyboardButton("Sunday", callback_data="toggle_day:sunday")],
                [InlineKeyboardButton("⏰ Set Time", callback_data="set_recurring_time")],
                [InlineKeyboardButton("Cancel", callback_data="recurring_cancel")]
            ]
            await query.edit_message_text(
                "Select weekdays for your recurring reminder:\n(Click to toggle selection)",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return
    
    elif query.data.startswith("select_date:"):
        selected_date = query.data.split(":", 1)[1]
        user_id = query.from_user.id
        

        
        # Check if this is for editing or creating
        if user_id in user_edit_context:
            # This is for editing
            user_edit_context[user_id]["selected_date"] = selected_date
            user_edit_context[user_id]["step"] = "waiting_for_edit_time"
            
            await query.edit_message_text(
                f"Date selected: {selected_date}\n\nPlease send the new time in HH:MM format (e.g., 14:30 or 2:30 PM):"
            )
            return SELECTING_TIME
        else:
            # This is for creating new reminder
            user_reminder_context[user_id] = {"date": selected_date, "step": "waiting_for_time"}
            
            await query.edit_message_text(
                f"Date selected: {selected_date}\n\nPlease send the time in HH:MM format (e.g., 14:30 or 2:30 PM):"
            )
    
    elif query.data == "select_all_days":
        user_id = query.from_user.id
        
        # Initialize user context if not exists
        if user_id not in user_reminder_context:
            user_reminder_context[user_id] = {"selected_days": [], "step": "selecting_days"}
        
        # Select all days
        all_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        user_reminder_context[user_id]["selected_days"] = all_days
        
        # Update keyboard with all days selected
        keyboard = []
        for day in all_days:
            keyboard.append([InlineKeyboardButton(f"✅ {day.title()}", callback_data=f"toggle_day:{day}")])
        
        # Add action buttons
        keyboard.append([InlineKeyboardButton("⏰ Set Time", callback_data="set_recurring_time")])
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="recurring_cancel")])
        
        await query.edit_message_text(
            f"Select weekdays for your recurring reminder:\n(Click to toggle selection)\n\nSelected: All days",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data.startswith("toggle_day:"):
        day_of_week = query.data.split(":", 1)[1]
        user_id = query.from_user.id
        
        # Initialize user context if not exists
        if user_id not in user_reminder_context:
            user_reminder_context[user_id] = {"selected_days": [], "step": "selecting_days"}
        
        # Toggle day selection
        selected_days = user_reminder_context[user_id].get("selected_days", [])
        if day_of_week in selected_days:
            selected_days.remove(day_of_week)
        else:
            selected_days.append(day_of_week)
        
        user_reminder_context[user_id]["selected_days"] = selected_days
        
        # Update keyboard with current selections
        keyboard = []
        all_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        
        # Check if all days are selected
        all_selected = all(day in selected_days for day in all_days)
        
        # Add "Every day" button
        if all_selected:
            keyboard.append([InlineKeyboardButton("Every day", callback_data="select_all_days")])
        else:
            keyboard.append([InlineKeyboardButton("Every day", callback_data="select_all_days")])
        
        for day in all_days:
            if day in selected_days:
                keyboard.append([InlineKeyboardButton(f"✅ {day.title()}", callback_data=f"toggle_day:{day}")])
            else:
                keyboard.append([InlineKeyboardButton(day.title(), callback_data=f"toggle_day:{day}")])
        
        # Add action buttons
        keyboard.append([InlineKeyboardButton("⏰ Set Time", callback_data="set_recurring_time")])
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="recurring_cancel")])
        
        if all_selected:
            selected_text = "All days"
        else:
            selected_text = ", ".join([day.title() for day in selected_days]) if selected_days else "None"
        
        await query.edit_message_text(
            f"Select weekdays for your recurring reminder:\n(Click to toggle selection)\n\nSelected: {selected_text}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data == "set_recurring_time":
        user_id = query.from_user.id
        if user_id in user_reminder_context and user_reminder_context[user_id].get("selected_days"):
            user_reminder_context[user_id]["step"] = "recurring_time_input"
            selected_days = user_reminder_context[user_id]["selected_days"]
            selected_text = ", ".join([day.title() for day in selected_days])
            await query.edit_message_text(
                f"Selected days: {selected_text}\n\nPlease send the time in HH:MM format (e.g., 14:30 or 2:30 PM):"
            )
        else:
            await query.edit_message_text("Please select at least one weekday first!")
    
    # Edit weekday selection handlers
    elif query.data == "edit_select_all_days":
        user_id = query.from_user.id
        
        # Select all days for editing
        all_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        user_edit_context[user_id]["selected_days"] = all_days
        
        # Update keyboard with all days selected
        keyboard = []
        for day in all_days:
            keyboard.append([InlineKeyboardButton(f"✅ {day.title()}", callback_data=f"edit_toggle_day:{day}")])
        
        # Add action buttons
        keyboard.append([InlineKeyboardButton("⏰ Set Time", callback_data="edit_set_recurring_time")])
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="edit_cancel")])
        
        await query.edit_message_text(
            f"Select weekdays for your recurring reminder (edit):\n(Click to toggle selection)\n\nSelected: All days",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data.startswith("edit_toggle_day:"):
        day_of_week = query.data.split(":", 1)[1]
        user_id = query.from_user.id
        
        # Toggle day selection for editing
        selected_days = user_edit_context[user_id].get("selected_days", [])
        if day_of_week in selected_days:
            selected_days.remove(day_of_week)
        else:
            selected_days.append(day_of_week)
        
        user_edit_context[user_id]["selected_days"] = selected_days
        
        # Update keyboard with current selections
        keyboard = []
        all_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        
        # Check if all days are selected
        all_selected = all(day in selected_days for day in all_days)
        
        # Add "Every day" button
        if all_selected:
            keyboard.append([InlineKeyboardButton("Every day", callback_data="edit_select_all_days")])
        else:
            keyboard.append([InlineKeyboardButton("Every day", callback_data="edit_select_all_days")])
        
        for day in all_days:
            if day in selected_days:
                keyboard.append([InlineKeyboardButton(f"✅ {day.title()}", callback_data=f"edit_toggle_day:{day}")])
            else:
                keyboard.append([InlineKeyboardButton(day.title(), callback_data=f"edit_toggle_day:{day}")])
        
        # Add action buttons
        keyboard.append([InlineKeyboardButton("⏰ Set Time", callback_data="edit_set_recurring_time")])
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="edit_cancel")])
        
        if all_selected:
            selected_text = "All days"
        else:
            selected_text = ", ".join([day.title() for day in selected_days]) if selected_days else "None"
        
        await query.edit_message_text(
            f"Select weekdays for your recurring reminder (edit):\n(Click to toggle selection)\n\nSelected: {selected_text}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data == "edit_set_recurring_time":
        user_id = query.from_user.id
        logging.info(f"edit_set_recurring_time called for user {user_id}")
        if user_id in user_edit_context and user_edit_context[user_id].get("selected_days"):
            user_edit_context[user_id]["step"] = "editing_recurring_time_input"
            selected_days = user_edit_context[user_id]["selected_days"]
            selected_text = ", ".join([day.title() for day in selected_days])
            logging.info(f"Set step to editing_recurring_time_input for user {user_id}, selected_days={selected_days}")
            await query.edit_message_text(
                f"Selected days: {selected_text}\n\nPlease send the new time in HH:MM format (e.g., 14:30 or 2:30 PM):"
            )
        else:
            logging.warning(f"User {user_id} not in edit context or no selected_days")
            await query.edit_message_text("Please select at least one weekday first!")
    
    elif query.data == "recurring_cancel":
        user_id = query.from_user.id
        if user_id in user_reminder_context:
            del user_reminder_context[user_id]
        await query.edit_message_text("Recurring reminder creation cancelled.")
    
    elif query.data == "one_time_cancel":
        user_id = query.from_user.id
        if user_id in user_edit_context:
            # This is for editing - use edit cancel logic
            del user_edit_context[user_id]
            await query.edit_message_text("Edit cancelled.")
        elif user_id in user_reminder_context:
            # This is for creating new reminder
            del user_reminder_context[user_id]
            await query.edit_message_text("One-time reminder creation cancelled.")
        else:
            # Fallback
            await query.edit_message_text("Operation cancelled.")
    
    elif query.data.startswith("calendar:"):
        # Handle calendar navigation
        year_month = query.data.split(":", 1)[1]
        year, month = map(int, year_month.split("-"))
        keyboard = create_calendar_keyboard(year, month)
        await query.edit_message_reply_markup(reply_markup=keyboard)
    
    elif query.data.startswith("delete_reminder:"):
        reminder_id = int(query.data.split(":", 1)[1])
        user_id = query.from_user.id
        
        # Get the job IDs before deleting
        job_ids = db.get_reminder_job_ids(reminder_id)
        
        if db.delete_reminder(reminder_id, user_id):
            # Remove from scheduler
            for job_id in job_ids:
                try:
                    scheduler.remove_job(job_id)
                    logging.info(f"Removed job {job_id} for deleted reminder {reminder_id}")
                except Exception as e:
                    logging.warning(f"Failed to remove job {job_id} for deleted reminder {reminder_id}: {e}")
            
            await query.edit_message_text(f"✅ Reminder {reminder_id} has been deleted.")
        else:
            await query.edit_message_text("❌ Reminder not found or you don't have permission to delete it.")
    
    elif query.data.startswith("edit_reminder_start:"):
        keyboard = []
        user_id = query.from_user.id
        chat_id = query.message.chat.id
        

        
        # Parse topic context from callback data
        topic_context = query.data.split(":", 1)[1]
        
        # Determine topic_id from context
        if topic_context == "general":
            topic_id = None
            topic_name = ""
        elif topic_context == "all":
            topic_id = None  # Get all reminders
            topic_name = "all topics"
        elif topic_context.startswith("topic_"):
            topic_id = int(topic_context.split("_", 1)[1])
            topic_name = f"Topic #{topic_id}"
        else:
            # Fallback to old logic
            topic_id = None
            topic_name = ""
            logging.warning(f"EDIT BUTTON - Unknown topic context: {topic_context}")
        
        # For general chats, we want all reminders (topic_id=None)
        # For topic chats, we want only reminders from that topic
        reminders = db.get_user_reminders(user_id, chat_id, topic_id)
        
        if not reminders:
            topic_info = f" in {topic_name}" if topic_id and topic_name else ""
            await query.edit_message_text(f"No reminders found{topic_info}. You can only edit/delete your own reminders.")
            return
        
        for reminder in reminders:
            reminder_id = reminder[0]
            message = reminder[1]
            # Truncate message if too long
            if len(message) > 30:
                message = message[:27] + "..."
            keyboard.append([InlineKeyboardButton(f"✏️ {reminder_id}: {message}", callback_data=f"edit_reminder:{reminder_id}")])
        
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="edit_cancel")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "Select a reminder to edit:",
            reply_markup=reply_markup
        )
    
    elif query.data.startswith("delete_reminder_start:"):
        keyboard = []
        user_id = query.from_user.id
        chat_id = query.message.chat.id
        
        # Parse topic context from callback data
        topic_context = query.data.split(":", 1)[1]
        
        # Determine topic_id from context
        if topic_context == "general":
            topic_id = None
            topic_name = ""
        elif topic_context == "all":
            topic_id = None  # Get all reminders
            topic_name = "all topics"
        elif topic_context.startswith("topic_"):
            topic_id = int(topic_context.split("_", 1)[1])
            topic_name = f"Topic #{topic_id}"
        else:
            # Fallback to old logic
            topic_id = None
            topic_name = ""
            logging.warning(f"DELETE BUTTON - Unknown topic context: {topic_context}")
        
        # For general chats, we want all reminders (topic_id=None)
        # For topic chats, we want only reminders from that topic
        reminders = db.get_user_reminders(user_id, chat_id, topic_id)
        
        if not reminders:
            topic_info = f" in {topic_name}" if topic_id and topic_name else ""
            await query.edit_message_text(f"No reminders found{topic_info}. You can only edit/delete your own reminders.")
            return
        
        for reminder in reminders:
            reminder_id = reminder[0]
            message = reminder[1]
            # Truncate message if too long
            if len(message) > 30:
                message = message[:27] + "..."
            keyboard.append([InlineKeyboardButton(f"🗑️ {reminder_id}: {message}", callback_data=f"delete_reminder:{reminder_id}")])
        
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="delete_cancel")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "Select a reminder to delete:",
            reply_markup=reply_markup
        )
    
    elif query.data.startswith("edit_reminder:"):
        reminder_id = int(query.data.split(":", 1)[1])
        user_id = query.from_user.id
        
        reminder = db.get_reminder_by_id(reminder_id, user_id)
        if not reminder:
            await query.edit_message_text("❌ Reminder not found or you don't have permission to edit it.")
            return
        
        # Store edit context
        user_edit_context[user_id] = {
            "reminder_id": reminder_id,
            "current_reminder": reminder
        }
        
        # Show edit options
        keyboard = [
            [InlineKeyboardButton("✏️ Edit Message", callback_data="edit_message")],
            [InlineKeyboardButton("⏰ Edit Time", callback_data="edit_time")],
            [InlineKeyboardButton("❌ Cancel", callback_data="edit_cancel")]
        ]
        
        reminder_id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, chat_id, topic_id = reminder
        
        # Format the time for display in the reminder's stored timezone
        try:
            # Resolve reminder's own timezone for display
            try:
                reminder_tz = pytz.timezone(timezone)
            except Exception:
                reminder_tz = pytz.UTC
            if is_recurring:
                # Convert stored remind_time to reminder's timezone and show HH:MM
                if isinstance(remind_time, str):
                    try:
                        reminder_dt = dateutil.parser.isoparse(remind_time)
                    except Exception:
                        reminder_dt = dateutil.parser.parse(remind_time)
                else:
                    reminder_dt = remind_time
                if reminder_dt.tzinfo is None:
                    reminder_dt = reminder_tz.localize(reminder_dt)
                time_display = reminder_dt.astimezone(reminder_tz).strftime("%H:%M")
            else:
                # One-time: display using reminder's stored timezone
                if isinstance(remind_time, str):
                    reminder_datetime = dateutil.parser.isoparse(remind_time)
                else:
                    reminder_datetime = remind_time
                if reminder_datetime.tzinfo is None:
                    reminder_datetime = reminder_tz.localize(reminder_datetime)
                time_display = reminder_datetime.astimezone(reminder_tz).strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            time_display = remind_time  # Fallback to original if parsing fails
            logging.error(f"Error parsing reminder time for display: {e}")
        
        # Convert IANA timezone to UTC offset for display
        timezone_display = timezone
        for offset, tz_name in UTC_OFFSET_TO_TZ.items():
            if tz_name == timezone:
                timezone_display = offset
                break
        
        edit_message = f"📝 Editing Reminder {reminder_id}:\n\n"
        edit_message += f"💬 Message: {message}\n"
        edit_message += f"⏰ Time: {time_display}\n"
        edit_message += f"🌍 Timezone: {timezone_display}\n"
        edit_message += f"🔄 Recurring: {'Yes' if is_recurring else 'No'}\n\n"
        edit_message += "What would you like to edit?"
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(edit_message, reply_markup=reply_markup)
    
    elif query.data == "close_list":
        await query.answer()
        await query.edit_message_text("Reminder list closed.")
    
    elif query.data == "edit_cancel":
        await query.answer()
        user_id = query.from_user.id
        if user_id in user_edit_context:
            del user_edit_context[user_id]
        await query.edit_message_text("Edit cancelled.")
    
    elif query.data == "delete_cancel":
        await query.answer()
        await query.edit_message_text("Delete cancelled.")
    
    elif query.data == "edit_message":
        user_id = query.from_user.id
        if user_id in user_edit_context:
            user_edit_context[user_id]["field_to_edit"] = "message"
            await query.edit_message_text("Please send the new message for your reminder (max 4000 characters):")
    
    elif query.data == "edit_time":
        user_id = query.from_user.id
        if user_id in user_edit_context:
            user_edit_context[user_id]["field_to_edit"] = "time"
            # Check if this is a recurring reminder
            reminder = user_edit_context[user_id]["current_reminder"]
            is_recurring = reminder[4]
            recurrence_type = reminder[5]
            day_of_week = reminder[6]
            if is_recurring:
                # Show weekday selection for recurring reminders
                # Parse selected days from day_of_week (comma-separated string)
                all_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
                
                # Map abbreviated day names to full day names
                day_mapping_reverse = {
                    'mon': 'monday',
                    'tue': 'tuesday', 
                    'wed': 'wednesday',
                    'thu': 'thursday',
                    'fri': 'friday',
                    'sat': 'saturday',
                    'sun': 'sunday'
                }
                
                if recurrence_type == "daily":
                    selected_days = all_days
                else:
                    # Convert abbreviated day names to full day names
                    abbreviated_days = day_of_week.split(",")
                    selected_days = [day_mapping_reverse.get(day, day) for day in abbreviated_days]
                    logging.info(f"Parsing days for edit: day_of_week='{day_of_week}', abbreviated_days={abbreviated_days}, selected_days={selected_days}")
                
                user_edit_context[user_id]["selected_days"] = selected_days
                user_edit_context[user_id]["step"] = "editing_selecting_days"
                
                # Create keyboard with current selections
                keyboard = []
                
                # Check if all days are selected
                all_selected = all(day in selected_days for day in all_days)
                
                # Add "Every day" button
                if all_selected:
                    keyboard.append([InlineKeyboardButton("Every day", callback_data="edit_select_all_days")])
                else:
                    keyboard.append([InlineKeyboardButton("Every day", callback_data="edit_select_all_days")])
                
                # Add individual day buttons with checkmarks for selected days
                for day in all_days:
                    if day in selected_days:
                        keyboard.append([InlineKeyboardButton(f"✅ {day.title()}", callback_data=f"edit_toggle_day:{day}")])
                    else:
                        keyboard.append([InlineKeyboardButton(day.title(), callback_data=f"edit_toggle_day:{day}")])
                
                # Add action buttons
                keyboard.append([InlineKeyboardButton("⏰ Set Time", callback_data="edit_set_recurring_time")])
                keyboard.append([InlineKeyboardButton("Cancel", callback_data="edit_cancel")])
                
                # Create selection text
                if all_selected:
                    selected_text = "All days"
                else:
                    selected_text = ", ".join([day.title() for day in selected_days]) if selected_days else "None"
                
                await query.edit_message_text(
                    f"Select weekdays for your recurring reminder (edit):\n(Click to toggle selection)\n\nSelected: {selected_text}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            else:
                # Show calendar for date selection (one-time reminder)
                today = datetime.now()
                keyboard = create_calendar_keyboard(today.year, today.month)
                
                try:
                    # Try to send message with topic support
                    await context.bot.send_message(
                        chat_id=query.message.chat.id,
                        text="Select a date for your reminder:",
                        reply_markup=keyboard,
                        message_thread_id=query.message.message_thread_id if hasattr(query.message, 'message_thread_id') and query.message.message_thread_id else None
                    )
                    await query.edit_message_text("Edit time - select a date:")
                except Exception as e:
                    # If topic fails, send to general chat
                    await context.bot.send_message(
                        chat_id=query.message.chat.id,
                        text="Select a date for your reminder:",
                        reply_markup=keyboard
                    )
                    await query.edit_message_text("Edit time - select a date:")
                
                return SELECTING_DATE
    

    

    
    elif query.data.startswith("setoffset:"):
        await query.answer()
        offset = query.data.split(":", 1)[1]
        tz_str = UTC_OFFSET_TO_TZ.get(offset)
        if tz_str:
            try:
                pytz.timezone(tz_str)
                # Always set timezone for the user, not the group
                user_timezones[query.from_user.id] = tz_str
                db.save_timezone_preference(query.from_user.id, 'user', tz_str)
                await query.edit_message_text(f"✅ Your timezone has been set to {offset}.")
                logging.info(f"User {query.from_user.id} set timezone to {tz_str} via offset {offset}")
            except Exception:
                await query.edit_message_text("Invalid offset selected. Please try again.")
                logging.error(f"User {query.from_user.id} tried to set invalid offset: {offset}")
        else:
            await query.edit_message_text("Unknown offset selected. Please try again.")
            logging.error(f"User {query.from_user.id} selected unknown offset: {offset}")
    
    elif query.data == "timezone_cancel":
        await query.answer()
        await query.edit_message_text("Timezone selection cancelled.")
    
    # Admin callback handlers
    elif query.data.startswith("admin_delete_start:"):
        await query.answer()
        # Get all reminders in the group for admin to delete
        chat_id = query.message.chat.id
        
        # Parse topic context from callback data
        parts = query.data.split(":", 1)
        if len(parts) == 2:
            topic_context = parts[1]
            if topic_context == "all":
                topic_id = None
                topic_name = ""
                logging.info("Callback: Getting ALL reminders (general topic)")
            elif topic_context.startswith("topic:"):
                topic_id = int(topic_context.split(":", 1)[1])
                topic_name = f"Topic #{topic_id}"
                logging.info(f"Callback: Getting reminders for specific topic {topic_id}")
            else:
                # Fallback to old method
                topic_id, topic_name = await get_topic_info_from_callback(query)
        else:
            # Fallback to old method
            topic_id, topic_name = await get_topic_info_from_callback(query)
        

        
        # If we're in general topic (topic_id is None or 1), get only general topic reminders
        # Otherwise, get reminders for specific topic
        if topic_id is None or topic_id == 1:
            reminders = db.get_general_topic_reminders(chat_id)  # Only general topic reminders
            topic_info = " (General Topic)"
        else:
            reminders = db.get_all_group_reminders(chat_id, topic_id)  # Specific topic
            topic_info = f" in {topic_name}"
        
        if not reminders:
            await query.edit_message_text(f"No reminders found{topic_info} to delete.")
            return
        
        # Create keyboard with reminders to delete
        keyboard = []
        for reminder in reminders[:20]:  # Limit to 20 reminders
            reminder_id, user_id, message_text, remind_time, timezone, is_recurring, recurrence_type, day_of_week, topic_id = reminder
            
            # Get user info
            try:
                user = await context.bot.get_chat_member(chat_id, user_id)
                user_name = user.user.first_name or user.user.username or f"User {user_id}"
            except:
                user_name = f"User {user_id}"
            
            # Truncate message if too long
            if len(message_text) > 30:
                message_text = message_text[:27] + "..."
            
            keyboard.append([InlineKeyboardButton(f"🗑️ {reminder_id}: {user_name} - {message_text}", callback_data=f"admin_delete_reminder:{reminder_id}")])
        
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="admin_delete_cancel")])
        
        await query.edit_message_text(
            f"🗑️ Select a reminder to delete{topic_info}:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data == "admin_delete_cancel":
        await query.answer()
        await query.edit_message_text("Delete operation cancelled.")
    
    elif query.data.startswith("admin_delete_reminder:"):
        reminder_id = int(query.data.split(":", 1)[1])
        chat_id = query.message.chat.id
        
        # Get reminder details
        reminder = db.get_reminder_by_id_admin(reminder_id, chat_id)
        if not reminder:
            await query.edit_message_text("❌ Reminder not found.")
            return
        
        # Get the job IDs before deleting
        job_ids = db.get_reminder_job_ids(reminder_id)
        
        # Delete the reminder
        if db.admin_delete_reminder(reminder_id, chat_id):
            # Remove from scheduler
            for job_id in job_ids:
                try:
                    scheduler.remove_job(job_id)
                    logging.info(f"Removed job {job_id} for deleted reminder {reminder_id}")
                except Exception as e:
                    logging.warning(f"Failed to remove job {job_id} for deleted reminder {reminder_id}: {e}")
            
            await query.edit_message_text(f"✅ Reminder {reminder_id} has been deleted by admin.")
        else:
            await query.edit_message_text("❌ Failed to delete reminder.")
    
    elif query.data == "admin_close":
        await query.answer()
        await query.edit_message_text("Admin panel closed.")
    
    else:
        await query.edit_message_text("Invalid selection. Please try again.")
        logging.error(f"User {query.from_user.id} made an invalid selection: {query.data}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_reminder_context:
        del user_reminder_context[user_id]
    if user_id in user_edit_context:
        del user_edit_context[user_id]
    await update.message.reply_text("Operation cancelled.")
    return

async def send_reminder(chat_id: int, message: str, reminder_id=None, topic_id=None, max_retries=None):
    if max_retries is None:
        max_retries = REMINDER_MAX_RETRIES
    """
    Send reminder with retry mechanism and exponential backoff.
    Returns True if successful, False if all retries failed.
    """
    logging.info(f"Sending reminder to chat_id={chat_id} topic_id={topic_id} (type: {'group' if str(chat_id).startswith('-') else 'private'}): {message}")
    
    for attempt in range(max_retries):
        try:
            if topic_id is not None:
                # Send to specific topic
                await main_application.bot.send_message(chat_id=chat_id, text=message, message_thread_id=topic_id)
                logging.info(f"Reminder sent to chat_id={chat_id} topic_id={topic_id}")
            else:
                # Send to general chat
                await main_application.bot.send_message(chat_id=chat_id, text=message)
                logging.info(f"Reminder sent to chat_id={chat_id}")
            
            # Success - mark as sent
            if reminder_id is not None:
                db.mark_reminder_sent(reminder_id)
            return True
            
        except BadRequest as e:
            if "Topic_closed" in str(e):
                logging.error(f"Topic closed error for reminder {reminder_id} to chat_id={chat_id} topic_id={topic_id}: {e}")
                # Don't retry for topic closed errors - the topic is permanently closed
                return False
            else:
                logging.error(f"BadRequest error for reminder {reminder_id} to chat_id={chat_id} topic_id={topic_id}: {e}")
                if attempt < max_retries - 1:
                    # Calculate exponential backoff delay: base^attempt seconds
                    delay = REMINDER_RETRY_DELAY_BASE ** attempt
                    logging.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                else:
                    # Final attempt failed
                    logging.error(f"All {max_retries} attempts failed for reminder {reminder_id} to chat_id={chat_id} topic_id={topic_id}")
                    return False
        except Exception as e:
            logging.error(f"Attempt {attempt + 1}/{max_retries} failed for reminder {reminder_id} to chat_id={chat_id} topic_id={topic_id}: {e}")
            
            if attempt < max_retries - 1:
                # Calculate exponential backoff delay: base^attempt seconds
                delay = REMINDER_RETRY_DELAY_BASE ** attempt
                logging.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
            else:
                # Final attempt failed
                logging.error(f"All {max_retries} attempts failed for reminder {reminder_id} to chat_id={chat_id} topic_id={topic_id}")
                return False
    
    return False

def schedule_reminder(chat_id: int, message: str, reminder_time, reminder_id=None, topic_id=None):
    global main_event_loop
    logging.info(f"schedule_reminder called for chat_id={chat_id} topic_id={topic_id} at {reminder_time} with message: {message}")
    try:
        if main_event_loop is not None:
            # Create a coroutine that handles the result
            async def send_with_result():
                success = await send_reminder(chat_id, message, reminder_id, topic_id)
                if not success:
                    logging.error(f"Reminder {reminder_id} failed to send after all retries")
                    # Could add additional handling here (e.g., notify admin, store in failed queue)
            
            asyncio.run_coroutine_threadsafe(
                send_with_result(),
                main_event_loop
            )
        else:
            logging.error("Main event loop is not set!")
    except Exception as e:
        logging.error(f"Failed to schedule reminder for chat_id={chat_id}: {e}")

def load_and_reschedule_pending_reminders(application):
    pending = db.get_pending_reminders()
    for row in pending:
        reminder_id, user_id, chat_id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, topic_id, job_id = row
        try:
            chat_id = int(chat_id)
            
            if is_recurring:
                # Handle recurring reminders
                try:
                    tz = pytz.timezone(timezone)
                except Exception:
                    tz = pytz.UTC
                
                # If jobs already exist in persistent store, skip rescheduling
                if recurrence_type == 'daily':
                    if job_id and scheduler.get_job(job_id):
                        logging.info(f"Skipping reschedule for recurring daily reminder {reminder_id}; job already exists")
                        continue
                elif recurrence_type == 'weekly':
                    existing_job_ids = db.get_reminder_job_ids(reminder_id) if job_id else []
                    day_abbrevs_expected = day_of_week.split(',') if day_of_week and ',' in day_of_week else ([day_of_week] if day_of_week else [])
                    if existing_job_ids:
                        all_exist = all(scheduler.get_job(jid) is not None for jid in existing_job_ids)
                        # If we have as many existing jobs as expected days and they all exist, skip
                        if all_exist and (len(existing_job_ids) == len(day_abbrevs_expected) if day_abbrevs_expected else True):
                            logging.info(f"Skipping reschedule for recurring weekly reminder {reminder_id}; jobs already exist")
                            continue

                # Extract hour and minute for recurring reminders from either a time string or a datetime
                try:
                    if isinstance(remind_time, str):
                        time_str = remind_time.strip()
                        try:
                            # Try ISO first
                            rt_dt = dateutil.parser.isoparse(time_str)
                        except Exception:
                            # Fallback to generic time parsing (handles HH:MM, HH:MM:SS, and AM/PM)
                            # Use today's date as default; tz-aware not needed for hour/minute extraction
                            rt_dt = dateutil.parser.parse(time_str)
                        hour, minute = rt_dt.hour, rt_dt.minute
                    else:
                        # Datetime object returned by DB driver
                        hour, minute = remind_time.hour, remind_time.minute
                except Exception as e:
                    logging.error(f"Failed to parse recurring remind_time for reminder {reminder_id}: {e}")
                    continue
                
                if recurrence_type == 'daily':
                    # Daily recurring reminder
                    job = scheduler.add_job(
                        schedule_reminder,
                        'cron',
                        hour=hour, minute=minute, timezone=tz,
                        args=[application, chat_id, message, None, reminder_id, topic_id]
                    )
                    # Store the new job ID if changed/missing
                    if not job_id or job_id != job.id:
                        db.update_reminder(reminder_id, user_id, job_id=job.id)
                elif recurrence_type == 'weekly':
                    # Weekly recurring reminder
                    if ',' in day_of_week:
                        # Multiple weekdays - create multiple jobs
                        day_abbrevs = day_of_week.split(',')
                        job_ids = []
                        for day_abbrev in day_abbrevs:
                            job = scheduler.add_job(
                                schedule_reminder,
                                'cron',
                                day_of_week=day_abbrev, hour=hour, minute=minute, timezone=tz,
                                args=[chat_id, message, None, reminder_id, topic_id]
                            )
                            job_ids.append(job.id)
                        # Store multiple job IDs
                        db.update_reminder(reminder_id, user_id, job_id=','.join(job_ids))
                    else:
                        # Single weekday
                        job = scheduler.add_job(
                            schedule_reminder,
                            'cron',
                            day_of_week=day_of_week, hour=hour, minute=minute, timezone=tz,
                            args=[chat_id, message, None, reminder_id, topic_id]
                        )
                        # Store the new job ID
                        if not job_id or job_id != job.id:
                            db.update_reminder(reminder_id, user_id, job_id=job.id)
                logging.info(f"Rescheduled recurring reminder {reminder_id} for chat_id={chat_id} topic_id={topic_id}")
                
            else:
                # Handle one-time reminders
                # Parse the stored time which may be a datetime or an ISO string
                if isinstance(remind_time, str):
                    reminder_time = dateutil.parser.isoparse(remind_time)
                else:
                    reminder_time = remind_time
                # Only reschedule if the time is still in the future
                if reminder_time > datetime.now(reminder_time.tzinfo):
                    # Remove old job if it exists
                    if job_id:
                        # Only remove if it exists (persistent store may already have it)
                        existing = scheduler.get_job(job_id)
                        if existing:
                            try:
                                scheduler.remove_job(job_id)
                                logging.info(f"Removed old job {job_id} for reminder {reminder_id}")
                            except Exception as e:
                                logging.warning(f"Failed to remove old job {job_id}: {e}")
                    
                    # Add new job
                    # Avoid duplicate if job with same id already exists
                    job = scheduler.add_job(
                        schedule_reminder,
                        'date',
                        run_date=reminder_time,
                        args=[chat_id, message, reminder_time, reminder_id, topic_id]
                    )
                    # Update job ID in database
                    db.update_reminder(reminder_id, user_id, job_id=job.id)
                    logging.info(f"Rescheduled reminder {reminder_id} for chat_id={chat_id} topic_id={topic_id} at {reminder_time}")
                else:
                    logging.info(f"Skipped past reminder {reminder_id} for chat_id={chat_id} at {reminder_time}")
                    
        except Exception as e:
            logging.error(f"Failed to reschedule reminder {reminder_id}: {e}")

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all active reminders for the user"""
    user_id = update.message.from_user.id  # Use the actual user who sent the command
    chat_id = update.effective_chat.id
    topic_id, topic_name = await get_topic_info(update, context)
    
    # Check if this is an anonymous message (sent as group)
    if update.message.from_user.is_bot and update.message.from_user.username == 'GroupAnonymousBot':
        await update.message.reply_text(
            "❌ Anonymous commands are not supported for /list\n\n"
            "Please send the /list command as yourself (not as the group) to view and manage your reminders.\n\n"
            "To disable 'Send as Group' for this bot:\n"
            "1. Go to group settings\n"
            "2. Find this bot in the admin list\n"
            "3. Disable 'Send as Group' option"
        )
        return
    

    
    # Check if user wants to see all topics or just current topic
    show_all_topics = context.args and context.args[0].lower() == 'all'
    
    if show_all_topics:
        # Get reminders for all topics in the chat
        reminders = db.get_user_reminders(user_id, chat_id)
        if not reminders:
            await update.message.reply_text("You have no active reminders in this group.")
            return
    else:
        # Get reminders for current topic only
        if topic_id is None or topic_id == 1:
            # Get only general topic reminders
            reminders = db.get_user_general_topic_reminders(user_id, chat_id)
        else:
            # Get reminders for specific topic
            reminders = db.get_user_reminders(user_id, chat_id, topic_id)
        
        if not reminders:
            topic_info = f" in {topic_name}" if topic_id and topic_id != 1 else ""
            await update.message.reply_text(f"You have no active reminders{topic_info}.")
            return
    
    # Note: For listing, we display each reminder in its own stored timezone, not the user's current timezone
    # So we won't compute a single tz for the whole list here.
    
    if show_all_topics:
        message = "📋 Your active reminders in this group:\n\n"
    else:
        topic_info = f" in {topic_name}" if topic_id and topic_name else ""
        message = f"📋 Your active reminders{topic_info}:\n\n"
    
    for reminder in reminders:
        reminder_id, msg, remind_time, timezone, is_recurring, recurrence_type, day_of_week, is_sent, reminder_topic_id = reminder
        
        # Parse the reminder time
        try:
            if is_recurring:
                # Display time in the reminder's own timezone (HH:MM)
                try:
                    tz_reminder = pytz.timezone(timezone)
                except Exception:
                    tz_reminder = pytz.UTC
                if isinstance(remind_time, str):
                    try:
                        reminder_dt = dateutil.parser.isoparse(remind_time)
                    except Exception:
                        reminder_dt = dateutil.parser.parse(remind_time)
                else:
                    reminder_dt = remind_time
                if reminder_dt.tzinfo is None:
                    reminder_dt = tz_reminder.localize(reminder_dt)
                display_time = reminder_dt.astimezone(tz_reminder).strftime("%H:%M")
                
                if recurrence_type == 'daily':
                    time_display = f"Every day at {display_time}"
                elif recurrence_type == 'weekly':
                    time_display = f"Every {day_of_week.title()} at {display_time}"
                else:
                    time_display = f"Recurring: {display_time}"
            else:
                # One-time: display in the reminder's own timezone
                try:
                    tz_reminder = pytz.timezone(timezone)
                except Exception:
                    tz_reminder = pytz.UTC
                if isinstance(remind_time, str):
                    reminder_datetime = dateutil.parser.isoparse(remind_time)
                else:
                    reminder_datetime = remind_time
                if reminder_datetime.tzinfo is None:
                    reminder_datetime = tz_reminder.localize(reminder_datetime)
                time_display = reminder_datetime.astimezone(tz_reminder).strftime("%Y-%m-%d %H:%M")
        except Exception as e:
            time_display = remind_time
            logging.error(f"Error parsing reminder time: {e}")
        
        reminder_type = "🔄" if is_recurring else "⏰"
        message += f"{reminder_type} ID: {reminder_id}\n"
        message += f"📅 {time_display}\n"
        message += f"💬 {msg[:50]}{'...' if len(msg) > 50 else ''}\n"
        
        # Add topic information when showing all reminders
        if show_all_topics and reminder_topic_id is not None:
            message += f"📌 Topic #{reminder_topic_id}\n"
        elif show_all_topics and reminder_topic_id is None:
            message += f"📌 General\n"
        
        message += "\n"
    
    # Add action buttons with topic context
    keyboard = []
    if show_all_topics:
        # For "all" view, use "all" context for edit/delete buttons
        topic_context = "all"
    else:
        topic_context = f"topic_{topic_id}" if topic_id else "general"
    keyboard.append([InlineKeyboardButton("✏️ Edit a reminder", callback_data=f"edit_reminder_start:{topic_context}")])
    keyboard.append([InlineKeyboardButton("🗑️ Delete a reminder", callback_data=f"delete_reminder_start:{topic_context}")])
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="close_list")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await update.message.reply_text(message, reply_markup=reply_markup)
    except BadRequest as e:
        if "Topic_closed" in str(e):
            await handle_topic_closed_error(update, context)
        else:
            raise e

async def delete_reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a reminder by ID"""
    # Check if this is an anonymous message (sent as group)
    if update.message.from_user.is_bot and update.message.from_user.username == 'GroupAnonymousBot':
        await update.message.reply_text(
            "❌ Anonymous commands are not supported for /delete\n\n"
            "Please send the /delete command as yourself (not as the group) to delete reminders.\n\n"
            "To disable 'Send as Group' for this bot:\n"
            "1. Go to group settings\n"
            "2. Find this bot in the admin list\n"
            "3. Disable 'Send as Group' option"
        )
        return
    
    if not context.args:
        try:
            await update.message.reply_text("Usage: /delete <reminder_id>\nUse /list to see your reminders.")
        except BadRequest as e:
            if "Topic_closed" in str(e):
                await handle_topic_closed_error(update, context)
            else:
                raise e
        return
    
    try:
        reminder_id = int(context.args[0])
        user_id = update.message.from_user.id  # Use the actual user who sent the command
        chat_id = update.effective_chat.id
        topic_id, topic_name = await get_topic_info(update, context)
        
        # First check if the reminder exists and belongs to the user
        reminder = db.get_reminder_by_id(reminder_id, user_id)
        if not reminder:
            await update.message.reply_text("❌ Reminder not found or you don't have permission to delete it.")
            return
        
        # Check if the reminder belongs to the current chat
        reminder_chat_id = reminder[7]  # chat_id is at index 7
        if reminder_chat_id != chat_id:
            await update.message.reply_text(f"❌ Reminder {reminder_id} not found in this chat.")
            return
        
        # Get the job IDs before deleting
        preselected_job_ids = db.get_reminder_job_ids(reminder_id)
        
        if db.delete_reminder(reminder_id, user_id):
            # Remove from scheduler
            for job_id in preselected_job_ids:
                try:
                    scheduler.remove_job(job_id)
                    logging.info(f"Removed job {job_id} for deleted reminder {reminder_id}")
                except Exception as e:
                    logging.warning(f"Failed to remove job {job_id} for deleted reminder {reminder_id}: {e}")
            
            await update.message.reply_text(f"✅ Reminder {reminder_id} has been deleted.")
        else:
            await update.message.reply_text("❌ Reminder not found or you don't have permission to delete it.")
    except ValueError:
        await update.message.reply_text("❌ Invalid reminder ID. Please provide a number.")

async def edit_reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the edit reminder conversation"""
    # Check if this is an anonymous message (sent as group)
    if update.message.from_user.is_bot and update.message.from_user.username == 'GroupAnonymousBot':
        await update.message.reply_text(
            "❌ Anonymous commands are not supported for /edit\n\n"
            "Please send the /edit command as yourself (not as the group) to edit reminders.\n\n"
            "To disable 'Send as Group' for this bot:\n"
            "1. Go to group settings\n"
            "2. Find this bot in the admin list\n"
            "3. Disable 'Send as Group' option"
        )
        return
    
    if not context.args:
        try:
            await update.message.reply_text("Usage: /edit <reminder_id>\nUse /list to see your reminders.")
        except BadRequest as e:
            if "Topic_closed" in str(e):
                await handle_topic_closed_error(update, context)
            else:
                raise e
        return
    
    try:
        reminder_id = int(context.args[0])
        user_id = update.message.from_user.id  # Use the actual user who sent the command
        chat_id = update.effective_chat.id
        topic_id, topic_name = await get_topic_info(update, context)
        
        reminder = db.get_reminder_by_id(reminder_id, user_id)
        if not reminder:
            await update.message.reply_text("❌ Reminder not found or you don't have permission to edit it.")
            return
        
        # Check if the reminder belongs to the current chat
        reminder_chat_id = reminder[7]  # chat_id is at index 7
        if reminder_chat_id != chat_id:
            await update.message.reply_text(f"❌ Reminder {reminder_id} not found in this chat.")
            return
        
        # Store edit context
        user_edit_context[user_id] = {
            "reminder_id": reminder_id,
            "current_reminder": reminder
        }
        
        # Show edit options
        keyboard = [
            [InlineKeyboardButton("✏️ Edit Message", callback_data="edit_message")],
            [InlineKeyboardButton("⏰ Edit Time", callback_data="edit_time")],
            [InlineKeyboardButton("❌ Cancel", callback_data="edit_cancel")]
        ]
        
        reminder_id, message, remind_time, timezone, is_recurring, recurrence_type, day_of_week, chat_id, topic_id = reminder
        
        # Format the time for display in the reminder's stored timezone
        try:
            try:
                reminder_tz = pytz.timezone(timezone)
            except Exception:
                reminder_tz = pytz.UTC
            if is_recurring:
                if isinstance(remind_time, str):
                    try:
                        reminder_dt = dateutil.parser.isoparse(remind_time)
                    except Exception:
                        reminder_dt = dateutil.parser.parse(remind_time)
                else:
                    reminder_dt = remind_time
                if reminder_dt.tzinfo is None:
                    reminder_dt = reminder_tz.localize(reminder_dt)
                time_display = reminder_dt.astimezone(reminder_tz).strftime("%H:%M")
            else:
                if isinstance(remind_time, str):
                    reminder_datetime = dateutil.parser.isoparse(remind_time)
                else:
                    reminder_datetime = remind_time
                if reminder_datetime.tzinfo is None:
                    reminder_datetime = reminder_tz.localize(reminder_datetime)
                time_display = reminder_datetime.astimezone(reminder_tz).strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            time_display = remind_time  # Fallback to original if parsing fails
            logging.error(f"Error parsing reminder time for display: {e}")
        
        # Convert IANA timezone to UTC offset for display
        timezone_display = timezone
        for offset, tz_name in UTC_OFFSET_TO_TZ.items():
            if tz_name == timezone:
                timezone_display = offset
                break
        
        edit_message = f"📝 Editing Reminder {reminder_id}:\n\n"
        edit_message += f"💬 Message: {message}\n"
        edit_message += f"⏰ Time: {time_display}\n"
        edit_message += f"🌍 Timezone: {timezone_display}\n"
        edit_message += f"🔄 Recurring: {'Yes' if is_recurring else 'No'}\n\n"
        edit_message += "What would you like to edit?"
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(edit_message, reply_markup=reply_markup)
        
        return EDITING_REMINDER
        
    except ValueError:
        await update.message.reply_text("❌ Invalid reminder ID. Please provide a number.")
        return

async def handle_edit_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle edit option selection"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if query.data == "edit_message":
        user_edit_context[user_id]["field_to_edit"] = "message"
        await query.edit_message_text("Please send the new message for your reminder (max 4000 characters):")
        return ENTERING_MESSAGE
    
    elif query.data == "edit_time":
        user_edit_context[user_id]["field_to_edit"] = "time"
        # Check if this is a recurring reminder
        reminder = user_edit_context[user_id]["current_reminder"]
        is_recurring = reminder[4]
        recurrence_type = reminder[5]
        day_of_week = reminder[6]
        if is_recurring:
            # Show weekday selection for recurring reminders
            # Parse selected days from day_of_week (comma-separated string)
            all_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
            
            # Map abbreviated day names to full day names
            day_mapping_reverse = {
                'mon': 'monday',
                'tue': 'tuesday', 
                'wed': 'wednesday',
                'thu': 'thursday',
                'fri': 'friday',
                'sat': 'saturday',
                'sun': 'sunday'
            }
            
            if recurrence_type == "daily":
                selected_days = all_days
            else:
                # Convert abbreviated day names to full day names
                abbreviated_days = day_of_week.split(",")
                selected_days = [day_mapping_reverse.get(day, day) for day in abbreviated_days]
            user_edit_context[user_id]["selected_days"] = selected_days
            user_edit_context[user_id]["step"] = "editing_selecting_days"
            
            # Create keyboard with current selections
            keyboard = []
            all_days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
            
            # Check if all days are selected
            all_selected = all(day in selected_days for day in all_days)
            
            # Add "Every day" button
            if all_selected:
                keyboard.append([InlineKeyboardButton("Every day", callback_data="edit_select_all_days")])
            else:
                keyboard.append([InlineKeyboardButton("Every day", callback_data="edit_select_all_days")])
            
            # Add individual day buttons with checkmarks for selected days
            for day in all_days:
                if day in selected_days:
                    keyboard.append([InlineKeyboardButton(f"✅ {day.title()}", callback_data=f"edit_toggle_day:{day}")])
                else:
                    keyboard.append([InlineKeyboardButton(day.title(), callback_data=f"edit_toggle_day:{day}")])
            
            # Add action buttons
            keyboard.append([InlineKeyboardButton("⏰ Set Time", callback_data="edit_set_recurring_time")])
            keyboard.append([InlineKeyboardButton("Cancel", callback_data="edit_cancel")])
            # Create selection text
            if all_selected:
                selected_text = "All days"
            else:
                selected_text = ", ".join([day.title() for day in selected_days]) if selected_days else "None"
            
            await query.edit_message_text(
                f"Select weekdays for your recurring reminder (edit):\n(Click to toggle selection)\n\nSelected: {selected_text}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        else:
            # Show calendar for date selection (one-time reminder)
            today = datetime.now()
            keyboard = create_calendar_keyboard(today.year, today.month)
            await query.edit_message_text("Edit cancelled.")
            await context.bot.send_message(
                chat_id=query.message.chat.id,
                text="Select a date for your reminder:",
                reply_markup=keyboard,
                message_thread_id=query.message.message_thread_id if hasattr(query.message, 'message_thread_id') and query.message.message_thread_id else None
            )
            return SELECTING_DATE
    

    
    elif query.data == "edit_cancel":
        if user_id in user_edit_context:
            del user_edit_context[user_id]
        await query.edit_message_text("Edit cancelled.")
        return

async def handle_edit_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input during editing"""
    user_id = update.effective_user.id
    
    if user_id not in user_edit_context:
        return
    
    edit_context = user_edit_context[user_id]
    field_to_edit = edit_context.get("field_to_edit")
    text = update.message.text
    
    if field_to_edit == "message":
        # Update the message
        reminder_id = edit_context["reminder_id"]
        if db.update_reminder(reminder_id, user_id, message=text):
            await update.message.reply_text(f"✅ Reminder {reminder_id} message updated to: {text}")
        else:
            await update.message.reply_text("❌ Failed to update reminder.")
        
        # Clear edit context
        del user_edit_context[user_id]
        return

async def handle_edit_date_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle date selection during editing"""
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("select_date:"):
        selected_date = query.data.split(":", 1)[1]
        user_id = query.from_user.id
        
        if user_id in user_edit_context:
            user_edit_context[user_id]["selected_date"] = selected_date
            user_edit_context[user_id]["step"] = "waiting_for_edit_time"
            
            await query.edit_message_text(
                f"Date selected: {selected_date}\n\nPlease send the new time in HH:MM format (e.g., 14:30 or 2:30 PM):"
            )
            return SELECTING_TIME
    
    elif query.data.startswith("calendar:"):
        # Handle calendar navigation
        year_month = query.data.split(":", 1)[1]
        year, month = map(int, year_month.split("-"))
        keyboard = create_calendar_keyboard(year, month)
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return SELECTING_DATE

async def handle_edit_time_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle time input during editing"""
    user_id = update.effective_user.id
    
    if user_id not in user_edit_context:
        return
    
    edit_context = user_edit_context[user_id]
    step = edit_context.get("step")
    text = update.message.text
    
    if step == "waiting_for_edit_time":
        try:
            parsed_time = parse_date(text, settings={'PREFER_DATES_FROM': 'future'})
            if parsed_time:
                time_str = parsed_time.strftime("%H:%M")
                selected_date = edit_context["selected_date"]
                
                # Combine date and time
                datetime_str = f"{selected_date} {time_str}"
                
                # Get timezone
                chat = update.effective_chat
                tz_str = get_user_timezone(user_id, chat.type, chat.id)
                
                try:
                    tz = pytz.timezone(tz_str)
                except Exception:
                    tz = pytz.UTC
                
                # Parse the combined datetime
                reminder_time = parse_date(datetime_str, settings={'PREFER_DATES_FROM': 'future', 'TIMEZONE': tz_str, 'RETURN_AS_TIMEZONE_AWARE': True})
                
                if not reminder_time:
                    await update.message.reply_text("Invalid date/time combination. Please try again.")
                    del user_edit_context[user_id]
                    return
                
                if reminder_time.tzinfo is None:
                    reminder_time = tz.localize(reminder_time)
                
                now = datetime.now(tz)
                if reminder_time < now:
                    await update.message.reply_text("The time is in the past. Please try again.")
                    del user_edit_context[user_id]
                    return
                
                # Update the reminder time in database
                reminder_id = edit_context["reminder_id"]
                if db.update_reminder(reminder_id, user_id, remind_time=reminder_time.isoformat()):
                    # Remove old scheduled job and add new one
                    # Note: In a production system, you'd want to store job IDs and remove them properly
                    # For now, we'll just add the new job
                    scheduler.add_job(
                        schedule_reminder, 
                        'date', 
                        run_date=reminder_time, 
                        args=[context.application, chat.id, edit_context["current_reminder"][1], reminder_time, reminder_id]
                    )
                    
                    await update.message.reply_text(
                        f"✅ Reminder {reminder_id} time updated to {reminder_time.strftime('%Y-%m-%d %H:%M:%S %Z')}!"
                    )
                else:
                    await update.message.reply_text("❌ Failed to update reminder.")
                
                # Clear edit context
                del user_edit_context[user_id]
                return
            else:
                await update.message.reply_text("Invalid time format. Please use HH:MM or 2:30 PM format:")
        except Exception as e:
            logging.error(f"Error parsing time: {e}")
            await update.message.reply_text("Invalid time format. Please use HH:MM or 2:30 PM format:")

async def check_admin_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is admin in the current chat"""
    chat = update.effective_chat
    user_id = update.effective_user.id
    
    # Allow all users to use admin commands in private chat (they'll get helpful message)
    if chat.type == "private":
        return True
    
    # For groups/supergroups, check admin status
    if chat.type not in ["group", "supergroup"]:
        return False
    
    try:
        chat_member = await context.bot.get_chat_member(chat.id, user_id)
        return chat_member.status in ['administrator', 'creator']
    except Exception as e:
        logging.error(f"Failed to check admin status for user {user_id} in chat {chat.id}: {e}")
        return False

async def admin_list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to view all reminders in the group"""
    # Check if this is an anonymous message (sent as group)
    if update.message.from_user.is_bot and update.message.from_user.username == 'GroupAnonymousBot':
        await update.message.reply_text(
            "❌ Anonymous commands are not supported for /adminlist\n\n"
            "Please send the /adminlist command as yourself (not as the group) to view reminders.\n\n"
            "To disable 'Send as Group' for this bot:\n"
            "1. Go to group settings\n"
            "2. Find this bot in the admin list\n"
            "3. Disable 'Send as Group' option"
        )
        return
    
    if not await check_admin_permissions(update, context):
        await update.message.reply_text(
            "❌ Admin Only Command\n\n"
            "Only administrators can use this command."
        )
        return
    
    # Handle private chat differently
    if update.effective_chat.type == "private":
        await update.message.reply_text(
            "📋 Your Reminders\n\n"
            "In private chat, you can manage your own reminders using:\n"
            "• /list - View your reminders\n"
            "• /delete <id> - Delete your reminder\n"
            "• /edit <id> - Edit your reminder\n\n"
            "Admin commands are designed for group management."
        )
        return
    
    chat_id = update.effective_chat.id
    topic_id, topic_name = await get_topic_info(update, context)
    
    # Check if user wants to see all topics or just current topic
    show_all_topics = context.args and context.args[0].lower() == 'all'
    
    if show_all_topics:
        # Get reminders for all topics in the chat
        reminders = db.get_all_group_reminders(chat_id)
        if not reminders:
            await update.message.reply_text("No reminders found in this group.")
            return
    else:
        # If we're in general topic (topic_id is None or 1), get only general topic reminders
        # Otherwise, get reminders for specific topic
        if topic_id is None or topic_id == 1:
            reminders = db.get_general_topic_reminders(chat_id)  # Only general topic reminders
        else:
            reminders = db.get_all_group_reminders(chat_id, topic_id)  # Specific topic
        
        if not reminders:
            # Fix the error message logic
            if topic_id is None or topic_id == 1:
                topic_info = " (General Topic)"
            else:
                topic_info = f" in {topic_name}"
            await update.message.reply_text(f"No reminders found{topic_info}.")
            return
    
    # Format reminders with user info
    if topic_id is None or topic_id == 1:
        message = "📋 Group Reminders (General Topic)\n\n"
    else:
        message = f"📋 Group Reminders{topic_name}\n\n"
    message += "💡 Admin Actions:\n"
    message += "• Click 🗑️ Delete to select a reminder to delete\n"
    message += "• Use /admindelete <id> for direct deletion\n\n"
    
    for reminder in reminders[:20]:  # Limit to 20 reminders
        reminder_id, user_id, message_text, remind_time, timezone, is_recurring, recurrence_type, day_of_week, topic_id = reminder
        
        # Get user info
        try:
            user = await context.bot.get_chat_member(chat_id, user_id)
            user_name = user.user.first_name or user.user.username or f"User {user_id}"
        except:
            user_name = f"User {user_id}"
        
        # Format time
        try:
            tz = pytz.timezone(timezone)
            if is_recurring:
                time_display = remind_time  # HH:MM format
            else:
                reminder_datetime = dateutil.parser.isoparse(remind_time)
                time_display = reminder_datetime.strftime("%Y-%m-%d %H:%M")
        except:
            time_display = remind_time
        
        # Add topic info if we're in general topic
        if topic_id is None or topic_id == 1:
            topic_info = f" (Topic {reminder[8]})" if reminder[8] else " (General)"
            message += f"{reminder_id} - {user_name}{topic_info}\n"
        else:
            message += f"{reminder_id} - {user_name}\n"
        
        message += f"⏰ {time_display} | 🔄 {'Yes' if is_recurring else 'No'}\n"
        message += f"💬 {message_text[:50]}{'...' if len(message_text) > 50 else ''}\n\n"
    
    # Add action buttons with topic context
    if topic_id is None or topic_id == 1:
        delete_callback = "admin_delete_start:all"
    else:
        delete_callback = f"admin_delete_start:topic:{topic_id}"
    
    keyboard = [
        [InlineKeyboardButton("🗑️ Delete", callback_data=delete_callback)],
        [InlineKeyboardButton("❌ Close", callback_data="admin_close")]
    ]
    
    try:
        await update.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except BadRequest as e:
        if "Topic_closed" in str(e):
            await handle_topic_closed_error(update, context)
        else:
            raise e

async def admin_delete_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to delete any reminder in the group"""
    # Check if this is an anonymous message (sent as group)
    if update.message.from_user.is_bot and update.message.from_user.username == 'GroupAnonymousBot':
        await update.message.reply_text(
            "❌ Anonymous commands are not supported for /admindelete\n\n"
            "Please send the /admindelete command as yourself (not as the group) to delete reminders.\n\n"
            "To disable 'Send as Group' for this bot:\n"
            "1. Go to group settings\n"
            "2. Find this bot in the admin list\n"
            "3. Disable 'Send as Group' option"
        )
        return
    
    if not await check_admin_permissions(update, context):
        await update.message.reply_text("❌ Admin Only Command")
        return
    
    # Handle private chat differently
    if update.effective_chat.type == "private":
        await update.message.reply_text(
            "🗑️ Delete Your Reminder\n\n"
            "In private chat, you can delete your own reminders using:\n"
            "• /delete <id> - Delete your reminder\n\n"
            "Admin commands are designed for group management."
        )
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /admindelete <reminder_id>")
        return
    
    try:
        reminder_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid reminder ID. Please provide a number.")
        return
    
    # Get reminder details
    reminder = db.get_reminder_by_id_admin(reminder_id, update.effective_chat.id)
    if not reminder:
        await update.message.reply_text("❌ Reminder not found.")
        return
    
    # Get the job IDs before deleting
    job_ids = db.get_reminder_job_ids(reminder_id)
    
    # Delete the reminder
    if db.admin_delete_reminder(reminder_id, update.effective_chat.id):
        # Remove from scheduler
        for job_id in job_ids:
            try:
                scheduler.remove_job(job_id)
                logging.info(f"Removed job {job_id} for deleted reminder {reminder_id}")
            except Exception as e:
                logging.warning(f"Failed to remove job {job_id} for deleted reminder {reminder_id}: {e}")
        
        await update.message.reply_text(f"✅ Reminder {reminder_id} has been deleted by admin.")
    else:
        await update.message.reply_text("❌ Failed to delete reminder.")



if __name__ == "__main__":
    import asyncio
    import dateutil.parser
    from telegram.error import TimedOut, NetworkError
    
    # Configure application with better timeout settings
    app = (ApplicationBuilder()
           .token(TOKEN)
           .get_updates_read_timeout(30)
           .get_updates_write_timeout(30)
           .get_updates_connect_timeout(30)
           .build())
    main_event_loop = asyncio.get_event_loop()
    main_application = app
    
    # Add error handler for network issues
    async def error_handler(update, context):
        """Handle network errors gracefully"""
        if isinstance(context.error, (TimedOut, NetworkError)):
            logging.warning(f"Network error: {context.error}")
            # Try to send a message to the user if possible
            if update and update.effective_chat:
                try:
                    await update.effective_chat.send_message("⚠️ Network timeout. Please try again in a moment.")
                except:
                    pass
        else:
            logging.error(f"Unhandled error: {context.error}")
    
    app.add_error_handler(error_handler)
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("remind", remind))
    app.add_handler(CommandHandler("settimezone", settimezone))
    app.add_handler(CommandHandler("list", list_reminders))
    app.add_handler(CommandHandler("delete", delete_reminder_command))
    app.add_handler(CommandHandler("edit", edit_reminder_command))
    app.add_handler(CommandHandler("help", help_command))
    
    # Admin commands
    app.add_handler(CommandHandler("adminlist", admin_list_reminders))
    app.add_handler(CommandHandler("admindelete", admin_delete_reminder))
    
    # Note-taking commands
    note_handlers = notes_bot.get_note_handlers()
    for handler in note_handlers:
        app.add_handler(handler)
    
    # Separate button handlers for notes and reminders
    app.add_handler(CallbackQueryHandler(notes_bot.note_button_handler, pattern="^(note_help|cancel_edit_note|close_notes|back_to_notes|view_note:|edit_note:|delete_note:|edit_note_start:|delete_note_start:|edit_note_title:)"))
    app.add_handler(CallbackQueryHandler(reminder_button, pattern="^(remind_type:|select_date:|select_all_days|toggle_day:|edit_toggle_day:|set_recurring_time|recurring_cancel|one_time_cancel|edit_reminder_start:|delete_reminder_start:|edit_reminder:|delete_reminder:|admin_delete_start:|admin_delete_reminder:|admin_delete_cancel|admin_close|close_list|calendar:|setoffset:|timezone_cancel|edit_message|edit_time|edit_cancel|delete_cancel|edit_select_all_days|edit_set_recurring_time|edit_toggle_day:)"))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reminder_text_input))
    
    # Reschedule pending reminders from DB
    load_and_reschedule_pending_reminders(app)
    
    # Run the bot
    app.run_polling() 