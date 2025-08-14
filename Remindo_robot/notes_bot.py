import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram.error import BadRequest
from datetime import datetime
import notes_db
import re

# Initialize database
notes_db.init_notes_db()

# Store user context for note editing
user_note_context = {}

# Configuration removed - using exact same logic as reminder system

def create_shareable_message_link(chat_id, message_id, chat_username=None, topic_id=None):
    """Create a shareable message link for public/private groups and forum topics.

    Adds ?thread=<topic_id> when provided so the link opens inside the correct forum topic.
    """
    if chat_username:
        link = f"https://t.me/{chat_username}/{message_id}"
    else:
        # Private group - create a link that works for group members
        chat_id_clean = str(chat_id).replace('-100', '')
        link = f"https://t.me/c/{chat_id_clean}/{message_id}"
    if topic_id:
        link = f"{link}?thread={topic_id}"
    return link

def get_supergroup_upgrade_message(chat_type):
    """Get the message explaining how to upgrade to supergroup"""
    return (
        f"❌ Note-taking requires supergroup\n\n"
        f"Make chat history visible for new members:\n"
        f"Group Settings → Chat history"
    )



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
            logging.info(f"Topic closed error handled silently - cannot send messages to closed topic")
        else:
            logging.error(f"BadRequest in topic closed error handler: {e}")
    except Exception as e:
        logging.error(f"Failed to send topic closed error message: {e}")

def get_topic_info_from_message(message):
    """Get topic information from a message object"""
    chat = message.chat
    topic_id = None
    topic_name = ""
    
    # Check if this is a topic message
    if hasattr(message, 'message_thread_id'):
        thread_id = message.message_thread_id
        msg_id = getattr(message, 'message_id', None)
        is_topic_message = getattr(message, 'is_topic_message', None)
        is_general_like = thread_id in (None, 0, 1) or (msg_id is not None and thread_id == msg_id)
        if chat.id < 0 and getattr(chat, 'is_forum', False):
            if (is_topic_message is not True) or is_general_like:
                topic_id = None
                topic_name = ""
            else:
                topic_id = thread_id
                topic_name = f"Topic #{topic_id}"
        else:
            topic_id = None
            topic_name = ""
    
    return topic_id, topic_name

async def get_topic_info(update, context=None):
    """Get topic information from the update"""
    chat = update.effective_chat
    topic_id = None
    topic_name = ""
    
    # Add detailed logging
    logging.info(f"get_topic_info - Chat ID: {chat.id}, Chat type: {chat.type}")
    logging.info(f"get_topic_info - Has message_thread_id: {hasattr(update.message, 'message_thread_id')}")
    logging.info(f"get_topic_info - Message thread ID: {getattr(update.message, 'message_thread_id', 'None')}")
    logging.info(f"get_topic_info - Is forum: {getattr(chat, 'is_forum', 'Unknown')}")
    logging.info(f"get_topic_info - Chat ID < 0: {chat.id < 0}")
    
    # Check if this is a topic message
    logging.info(f"get_topic_info - Condition check: hasattr = {hasattr(update.message, 'message_thread_id')}, message_thread_id = {getattr(update.message, 'message_thread_id', 'None')}")
    if hasattr(update.message, 'message_thread_id'):
        thread_id = update.message.message_thread_id
        msg_id = getattr(update.message, 'message_id', None)
        is_topic_message = getattr(update.message, 'is_topic_message', None)
        logging.info(f"get_topic_info - message_id: {msg_id}, is_topic_message: {is_topic_message}")
        # In some Telegram client/API cases, messages in the general topic may carry
        # a message_thread_id equal to the current message_id. Treat those as general.
        is_general_like = thread_id in (None, 0, 1) or (msg_id is not None and thread_id == msg_id)
        if chat.id < 0 and getattr(chat, 'is_forum', False):
            logging.info("get_topic_info - Forum group detected")
            # Prefer explicit PTB flag if available; treat None as general-safe
            if (is_topic_message is not True) or is_general_like:
                topic_id = None
                topic_name = ""
                logging.info(f"get_topic_info - General chat in forum detected (thread_id={thread_id}, message_id={msg_id})")
            else:
                topic_id = thread_id
                topic_name = f"Topic #{topic_id}"
                logging.info(f"get_topic_info - Topic chat in forum (message_thread_id = {thread_id})")
        else:
            topic_id = None
            topic_name = ""
            logging.info("get_topic_info - Regular group chat (not forum)")
    else:
        logging.info("get_topic_info - No message_thread_id attribute on message")
    
    logging.info(f"get_topic_info - Final topic_id: {topic_id}, topic_name: {topic_name}")
    return topic_id, topic_name

async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /note command - save a message as a note"""
    # Check if this is an anonymous message (sent as group)
    if update.message.from_user.is_bot and update.message.from_user.username == 'GroupAnonymousBot':
        await update.message.reply_text(
            "❌ Anonymous commands are not supported for /note\n\n"
            "Please send the /note command as yourself (not as the group) to save notes.\n\n"
            "To disable 'Send as Group' for this bot:\n"
            "1. Go to group settings\n"
            "2. Find this bot in the admin list\n"
            "3. Disable 'Send as Group' option"
        )
        return
    
    # Check if this is a supergroup
    chat = update.effective_chat
    if chat.type != "supergroup":
        await update.message.reply_text(get_supergroup_upgrade_message(chat.type))
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    topic_id, topic_name = await get_topic_info(update, context)
    
    # Add detailed logging for note creation
    logging.info(f"note_command - User: {user_id}, Chat: {chat_id}")
    logging.info(f"note_command - Message thread ID: {getattr(update.message, 'message_thread_id', 'None')}")
    logging.info(f"note_command - Topic ID: {topic_id}, Topic name: {topic_name}")
    logging.info(f"note_command - Is forum: {getattr(update.effective_chat, 'is_forum', 'Unknown')}")
    
    # Check if user wants to force general chat mode
    if context.args and context.args[0].lower() == "general":
        if chat.is_forum:
            topic_id = None
            topic_name = ""
            logging.info(f"note_command - User forced general chat mode")
        context.args = context.args[1:]  # Remove "general" from args
    
    # Check if this is a reply to another message
    if update.message.reply_to_message:
        # Check if this is a forum topic auto-reply (reply message ID == topic ID)
        replied_message = update.message.reply_to_message
        if topic_id and replied_message.message_id == topic_id:
            # This is an auto-reply to topic starter in forum - treat as no reply
            # Show usage instructions - only reply is supported
            await update.message.reply_text(
                "📝 Save a Note\n\n"
                "To save a note:\n\n"
                "1️⃣ Reply to a message with /note to save that message\n"
                "2️⃣ Add a title: Reply with /note Your Title to save with a title\n\n"
                "Examples:\n"
                "• Reply to any message with /note\n"
                "• Reply with /note Important meeting"
            )
            return
        
        # Save the replied message as a note
        message_text = replied_message.text or replied_message.caption or "Media message"
        message_id = replied_message.message_id
        
        # Generate message link using the new function
        chat_username = update.effective_chat.username
        message_link = create_shareable_message_link(chat_id, message_id, chat_username, topic_id)
        
        # Get note title from command arguments
        title = None
        if context.args:
            title = " ".join(context.args)
        
        # Add the note
        note_id = notes_db.add_note(
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            message_text=message_text,
            message_link=message_link,
            topic_id=topic_id,
            title=title
        )
        
        topic_info = f" in {topic_name}" if topic_id else ""
        title_info = f" with title: {title}" if title else ""
        

        
        await update.message.reply_text(
            f"✅ Note saved successfully{topic_info}{title_info}!\n\n"
            f"📝 Note ID: {note_id}\n"
            f"🔗 Message: {message_link}\n\n"
            f"Use /notes to view your notes in this topic."
        )
        
    else:
        # No reply - show usage instructions (note-taking is reply-only)
        await update.message.reply_text(
            "📝 Save a Note\n\n"
            "To save a note:\n\n"
            "1️⃣ Reply to a message with /note to save that message\n"
            "2️⃣ Add a title: Reply with /note Your Title to save with a title\n\n"
            "Examples:\n"
            "• Reply to any message with /note\n"
            "• Reply with /note Important meeting"
        )

async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /notes command - list user's notes"""
    # Check if this is an anonymous message (sent as group)
    if update.message.from_user.is_bot and update.message.from_user.username == 'GroupAnonymousBot':
        await update.message.reply_text(
            "❌ Anonymous commands are not supported for /notes\n\n"
            "Please send the /notes command as yourself (not as the group) to view notes.\n\n"
            "To disable 'Send as Group' for this bot:\n"
            "1. Go to group settings\n"
            "2. Find this bot in the admin list\n"
            "3. Disable 'Send as Group' option"
        )
        return
    
    # Check if this is a supergroup
    chat = update.effective_chat
    if chat.type != "supergroup":
        await update.message.reply_text(get_supergroup_upgrade_message(chat.type))
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    topic_id, topic_name = await get_topic_info(update, context)
    
    # Add detailed logging
    logging.info(f"Notes command - User: {user_id}, Chat: {chat_id}, Chat type: {chat.type}")
    logging.info(f"Notes command - Message thread ID: {getattr(update.message, 'message_thread_id', 'None')}")
    logging.info(f"Notes command - Is forum: {getattr(chat, 'is_forum', 'Unknown')}")
    logging.info(f"Notes command - Topic ID: {topic_id}, Topic name: {topic_name}")
    
    # Check if user wants to see all notes or just current topic
    show_all = len(context.args) > 0 and context.args[0].lower() == 'all'
    logging.info(f"Notes command - Show all: {show_all}")
    
    if show_all:
        # Get all notes in the chat
        notes = notes_db.get_all_user_notes_in_chat(user_id, chat_id)
        topic_info = " (all topics)"
        logging.info(f"Notes command - Retrieved {len(notes)} notes for all topics")
    else:
        # Get notes for current topic
        # Special handling for general chat in forum groups
        if topic_id is None and chat.is_forum:
            # In forum groups, general chat notes might be saved with topic_id = 1 or NULL
            logging.info(f"Notes command - Using forum general chat query")
            notes = notes_db.get_user_notes_forum_general(user_id, chat_id)
            logging.info(f"Notes command - Retrieved {len(notes)} notes for forum general chat")
        else:
            # Get notes for current topic
            logging.info(f"Notes command - Using regular topic query with topic_id: {topic_id}")
            notes = notes_db.get_user_notes(user_id, chat_id, topic_id)
            logging.info(f"Notes command - Retrieved {len(notes)} notes for topic {topic_id}")
        topic_info = f" in {topic_name}" if topic_id else ""
    
    if not notes:
        topic_display = "all topics" if show_all else (topic_name if topic_id else "this topic")
        await update.message.reply_text(
            f"📝 No notes found in {topic_display}.\n\n"
            f"To save a note:\n"
            f"• Reply to any message with /note\n"
            f"• Or use /note Your note text"
        )
        return
    
    # Format notes for display
    notes_text = f"📝 Your Notes{topic_info}:\n\n"
    
    for i, note in enumerate(notes, 1):
        note_id, message_text, message_link, title, description, created_at, note_topic_id = note
        
        # Truncate message text if too long
        display_text = message_text[:100] + "..." if len(message_text) > 100 else message_text
        
        # Add topic info if showing all notes
        topic_display = ""
        if show_all and note_topic_id:
            topic_display = f" (Topic #{note_topic_id})"
        
        notes_text += f"{i}. Note #{note_id}{topic_display}\n"
        if title:
            notes_text += f"   📌 {title}\n"
        notes_text += f"   📄 {display_text}\n"
        
        # Add message link (only supergroups are allowed)
        notes_text += f"   🔗 [View Message]({message_link})\n\n"
    
    # Add pagination info if needed
    if len(notes) > 10:
        notes_text += f"\n📊 Showing {len(notes)} notes. Use /notes all to see all notes in this group."
    
    # Create inline keyboard for note actions
    keyboard = []
    if notes:
        # Add action buttons similar to reminder system
        if show_all:
            topic_context = "all"
        else:
            topic_context = f"topic_{topic_id}" if topic_id else "general"
        
        keyboard.append([InlineKeyboardButton("✏️ Edit a note", callback_data=f"edit_note_start:{topic_context}")])
        keyboard.append([InlineKeyboardButton("🗑️ Delete a note", callback_data=f"delete_note_start:{topic_context}")])
        keyboard.append([InlineKeyboardButton("❌ Close", callback_data="close_notes")])
    else:
        keyboard.append([InlineKeyboardButton("❌ Close", callback_data="close_notes")])
    
    try:
        await update.message.reply_text(
            notes_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except BadRequest as e:
        if "Topic_closed" in str(e):
            await handle_topic_closed_error(update, context)
        else:
            # Fallback without markdown
            await update.message.reply_text(
                notes_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

async def deletenote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /deletenote command - delete a specific note"""
    # Check if this is an anonymous message (sent as group)
    if update.message.from_user.is_bot and update.message.from_user.username == 'GroupAnonymousBot':
        await update.message.reply_text(
            "❌ Anonymous commands are not supported for /deletenote\n\n"
            "Please send the /deletenote command as yourself (not as the group) to delete notes.\n\n"
            "To disable 'Send as Group' for this bot:\n"
            "1. Go to group settings\n"
            "2. Find this bot in the admin list\n"
            "3. Disable 'Send as Group' option"
        )
        return
    
    # Check if this is a supergroup
    chat = update.effective_chat
    if chat.type != "supergroup":
        await update.message.reply_text(get_supergroup_upgrade_message(chat.type))
        return
    
    if not context.args:
        await update.message.reply_text(
            "🗑️ Delete Note\n\n"
            "Usage: /deletenote <note_id>\n\n"
            "Examples:\n"
            "• /deletenote 123 - Delete note with ID 123\n"
            "• Use /notes to see your notes and their IDs"
        )
        return
    
    try:
        note_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid note ID. Please provide a number.")
        return
    
    user_id = update.effective_user.id
    
    # Check if note exists and belongs to user
    note = notes_db.get_note_by_id(note_id, user_id)
    if not note:
        await update.message.reply_text("❌ Note not found or you don't have permission to delete it.")
        return
    
    # Delete the note
    if notes_db.delete_note(note_id, user_id):
        await update.message.reply_text(f"✅ Note #{note_id} has been deleted successfully.")
    else:
        await update.message.reply_text("❌ Failed to delete note.")

async def editnote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /editnote command - edit a note's title"""
    # Check if this is an anonymous message (sent as group)
    if update.message.from_user.is_bot and update.message.from_user.username == 'GroupAnonymousBot':
        await update.message.reply_text(
            "❌ Anonymous commands are not supported for /editnote\n\n"
            "Please send the /editnote command as yourself (not as the group) to edit notes.\n\n"
            "To disable 'Send as Group' for this bot:\n"
            "1. Go to group settings\n"
            "2. Find this bot in the admin list\n"
            "3. Disable 'Send as Group' option"
        )
        return
    
    # Check if this is a supergroup
    chat = update.effective_chat
    if chat.type != "supergroup":
        await update.message.reply_text(get_supergroup_upgrade_message(chat.type))
        return
    
    if not context.args:
        await update.message.reply_text(
            "✏️ Edit Note\n\n"
            "Usage: /editnote <note_id>\n\n"
            "Examples:\n"
            "• /editnote 123 - Edit note with ID 123\n"
            "• Use /notes to see your notes and their IDs"
        )
        return
    
    try:
        note_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid note ID. Please provide a number.")
        return
    
    user_id = update.effective_user.id
    
    # Check if note exists and belongs to user
    note = notes_db.get_note_by_id(note_id, user_id)
    if not note:
        await update.message.reply_text("❌ Note not found or you don't have permission to edit it.")
        return
    
    # Store note info in user context
    user_note_context[user_id] = {
        "note_id": note_id,
        "step": "editing_note",
        "note_data": note
    }
    
    # Show edit options
    note_id, user_id, chat_id, topic_id, message_id, message_text, message_link, title, description, created_at, updated_at = note
    
    edit_text = f"✏️ Edit Note #{note_id}\n\n"
    edit_text += f"📄 Current content:\n{message_text[:200]}{'...' if len(message_text) > 200 else ''}\n\n"
    
    if title:
        edit_text += f"📌 Current title: {title}\n"
    else:
        edit_text += f"📌 Current title: None\n"
    
    edit_text += f"\n🔗 Message link: {message_link}\n\n"
    edit_text += "What would you like to edit?"
    
    keyboard = [
        [InlineKeyboardButton("📌 Edit Title", callback_data=f"edit_note_title:{note_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_edit_note")]
    ]
    
    try:
        await update.message.reply_text(
            edit_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except BadRequest as e:
        if "Topic_closed" in str(e):
            await handle_topic_closed_error(update, context)
        else:
            # Fallback without markdown
            await update.message.reply_text(
                edit_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

async def show_notes_for_action(query, user_id, action, topic_context):
    """Show notes for edit or delete action"""
    chat_id = query.message.chat.id
    
    # Parse topic context
    if topic_context == "all":
        notes = notes_db.get_all_user_notes_in_chat(user_id, chat_id)
        topic_info = " (all topics)"
    elif topic_context.startswith("topic_"):
        topic_id = int(topic_context.split("_")[1])
        notes = notes_db.get_user_notes(user_id, chat_id, topic_id)
        topic_info = f" (Topic #{topic_id})"
    else:
        notes = notes_db.get_user_notes(user_id, chat_id, None)
        topic_info = ""
    
    if not notes:
        action_text = "edit" if action == "edit" else "delete"
        await query.edit_message_text(f"📝 No notes found to {action_text}{topic_info}.")
        return
    
    # Format notes for display
    action_text = "Edit" if action == "edit" else "Delete"
    notes_text = f"📝 Select a note to {action_text.lower()}{topic_info}:\n\n"
    
    for i, note in enumerate(notes, 1):
        note_id, message_text, message_link, title, description, created_at, note_topic_id = note
        
        # Truncate message text if too long
        display_text = message_text[:50] + "..." if len(message_text) > 50 else message_text
        
        notes_text += f"{i}. Note #{note_id}\n"
        if title:
            notes_text += f"   📌 {title}\n"
        notes_text += f"   📄 {display_text}\n\n"
    
    # Create keyboard with note buttons
    keyboard = []
    action_buttons = []
    for i, note in enumerate(notes[:10], 1):  # Limit to first 10 notes
        note_id = note[0]
        action_buttons.append(InlineKeyboardButton(f"#{note_id}", callback_data=f"{action}_note:{note_id}"))
        
        if len(action_buttons) >= 5:  # 5 buttons per row
            keyboard.append(action_buttons)
            action_buttons = []
    
    if action_buttons:
        keyboard.append(action_buttons)
    
    # Add back button
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_notes")])
    
    try:
        await query.edit_message_text(notes_text, reply_markup=InlineKeyboardMarkup(keyboard))
    except BadRequest:
        await query.edit_message_text(notes_text, reply_markup=InlineKeyboardMarkup(keyboard))

async def note_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks for note actions"""
    query = update.callback_query
    
    try:
        await query.answer()
    except Exception as e:
        logging.warning(f"Failed to answer callback query: {e}")
        # Continue processing even if answer fails
    
    user_id = query.from_user.id
    data = query.data
    

    
    if data == "note_help":
        help_text = (
            "📝 Note Taking Help\n\n"
            "Commands:\n"
            "• /note - Save a message as a note (reply to message)\n"
            "• /note <title> - Save with a title\n"
            "• /note <text> - Create a new note\n"
            "• /notes - View notes in current topic\n"
            "• /notes all - View all notes in group\n"
            "• /deletenote <id> - Delete a note\n"
            "• /editnote <id> - Edit note title\n\n"
            "Tips:\n"
            "• Reply to any message with /note to save it\n"
            "• Notes are organized by topics in forum groups\n"
            "• Click message links to navigate to original messages\n"
            "• Only available in supergroups\n\n"
            "Supergroup Required:\n"
            "• Note-taking is only available in supergroups\n"
            "• Upgrade your group to supergroup to enable this feature\n"
            "• Message links work reliably in supergroups"
        )
        
        await query.edit_message_text(help_text, parse_mode='Markdown')
        return
    
    if data == "cancel_edit_note":
        if user_id in user_note_context:
            del user_note_context[user_id]
        await query.edit_message_text("❌ Note editing cancelled.")
        return
    
    # Handle view note
    if data.startswith("view_note:"):
        note_id = int(data.split(":")[1])
        note = notes_db.get_note_by_id(note_id, user_id)
        if note:
            note_id, user_id, chat_id, topic_id, message_id, message_text, message_link, title, description, created_at, updated_at = note
            
            view_text = f"📝 Note #{note_id}\n\n"
            view_text += f"📄 Content:\n{message_text}\n\n"
            
            if title:
                view_text += f"📌 Title: {title}\n"
            
            # Handle message link display (only supergroups are allowed)
            view_text += f"🔗 Message Link: {message_link}\n"
            view_text += f"📅 Created: {created_at[:19]}\n"
            
            keyboard = [[InlineKeyboardButton("🔗 Open Message", url=message_link)]]
            keyboard.append([InlineKeyboardButton("✏️ Edit", callback_data=f"edit_note:{note_id}")])
            keyboard.append([InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_note:{note_id}")])
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_notes")])
            
            try:
                await query.edit_message_text(view_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            except BadRequest:
                await query.edit_message_text(view_text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text("❌ Note not found.")
        return
    
    # Handle edit note
    if data.startswith("edit_note:"):
        note_id = int(data.split(":")[1])
        note = notes_db.get_note_by_id(note_id, user_id)
        if note:
            user_note_context[user_id] = {
                "note_id": note_id,
                "step": "editing_note",
                "note_data": note
            }
            
            note_id, user_id, chat_id, topic_id, message_id, message_text, message_link, title, description, created_at, updated_at = note
            
            edit_text = f"✏️ Edit Note #{note_id}\n\n"
            edit_text += f"📄 Content:\n{message_text[:200]}{'...' if len(message_text) > 200 else ''}\n\n"
            
            if title:
                edit_text += f"📌 Current title: {title}\n"
            else:
                edit_text += f"📌 Current title: None\n"
            
            edit_text += f"\n🔗 Message link: {message_link}\n\n"
            edit_text += "What would you like to edit?"
            
            keyboard = [
                [InlineKeyboardButton("📌 Edit Title", callback_data=f"edit_note_title:{note_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_edit_note")]
            ]
            
            try:
                await query.edit_message_text(edit_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            except BadRequest:
                await query.edit_message_text(edit_text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.edit_message_text("❌ Note not found.")
        return
    
    # Handle close notes
    if data == "close_notes":
        await query.edit_message_text("📝 Notes closed.")
        return
    
    # Handle back to notes
    if data == "back_to_notes":
        # This would need to be handled by recreating the notes list
        # For now, just close the message
        await query.edit_message_text("📝 Use /notes to view your notes again.")
        return
    
    # Handle edit note start (show notes to edit)
    if data.startswith("edit_note_start:"):
        topic_context = data.split(":", 1)[1]
        await show_notes_for_action(query, user_id, "edit", topic_context)
        return
    
    # Handle delete note start (show notes to delete)
    if data.startswith("delete_note_start:"):
        topic_context = data.split(":", 1)[1]
        await show_notes_for_action(query, user_id, "delete", topic_context)
        return
    
    # Handle delete note
    if data.startswith("delete_note:"):
        note_id = int(data.split(":")[1])
        if notes_db.delete_note(note_id, user_id):
            await query.edit_message_text(f"✅ Note #{note_id} has been deleted successfully.")
        else:
            await query.edit_message_text("❌ Failed to delete note.")
        return
    
    # Handle edit note title
    if data.startswith("edit_note_title:"):
        note_id = int(data.split(":")[1])
        if user_id in user_note_context:
            user_note_context[user_id]["step"] = "editing_title"
            cancel_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_edit_note")
            ]])
            await query.edit_message_text(
                f"📌 Edit Title for Note #{note_id}\n\n"
                f"Please send the new title for your note.",
                reply_markup=cancel_keyboard
            )
        return
    


async def handle_note_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input for note editing"""
    user_id = update.effective_user.id
    
    if user_id not in user_note_context:
        return
    
    user_context = user_note_context[user_id]
    step = user_context.get("step")
    
    if step == "editing_title":
        note_id = user_context["note_id"]
        new_title = update.message.text
        
        if notes_db.update_note(note_id, user_id, title=new_title):
            await update.message.reply_text(f"✅ Title updated successfully for Note #{note_id}!")
        else:
            await update.message.reply_text("❌ Failed to update title.")
        
        # Clear user context
        del user_note_context[user_id]

# Command handlers to be registered in main bot
def get_note_handlers():
    """Return list of note-related command handlers"""
    return [
        CommandHandler("note", note_command),
        CommandHandler("notes", notes_command),
        CommandHandler("deletenote", deletenote_command),
        CommandHandler("editnote", editnote_command)
    ] 