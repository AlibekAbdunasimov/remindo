# 🤖 Remindo Bot

A powerful Telegram reminder bot with integrated note-taking functionality, supporting one-time and recurring reminders, timezone management, and forum group integration.

## ✨ Features

### 📝 Reminder Management
- **One-time reminders**: Set reminders for specific dates and times
- **Recurring reminders**: Daily and weekly recurring reminders
- **Interactive creation**: Button-based interface for creating reminders
- **Calendar interface**: Visual date selection with navigation
- **Flexible time formats**: Supports HH:MM (24-hour) and AM/PM formats

### 🔄 Recurring Reminder Types
- **Daily reminders**: "Every day at [time]"
- **Weekly reminders**: "Every week on [day] at [time]"
- **Multi-day weekly**: Select multiple weekdays (e.g., Monday, Wednesday, Friday)

### 📋 Reminder Management
- **List reminders**: `/list` - View reminders in current topic
- **List all**: `/list all` - View all reminders in the group
- **Edit reminders**: `/edit <id>` - Edit message or time
- **Delete reminders**: `/delete <id>` - Delete specific reminders
- **Topic organization**: Reminders automatically organized by topics in forum groups

### 📝 Note-Taking System
- **Message capture**: Reply to any message with `/note` to save it as a note
- **Title support**: Add titles when saving notes: Reply with `/note Your Title`
- **Topic organization**: Notes automatically organized by forum topics
- **Rich metadata**: Stores message links, titles, and creation timestamps
- **Search & management**: List, edit, and delete notes easily
- **Forum integration**: Seamless topic-aware note organization

### ⚙️ Settings & Configuration
- **Timezone support**: `/settimezone` - Set your personal timezone in private chat
- **User-specific timezones**: Each user sets their own timezone preference
- **Timezone persistence**: Stored in database for each user
- **UTC offset selection**: 39 different timezone options

### 🏛️ Forum Group Support
- **Topic-aware reminders**: Reminders automatically associated with topics
- **Topic-aware notes**: Notes automatically organized by forum topics
- **Topic-specific listing**: View reminders and notes by topic
- **Cross-topic management**: List all reminders/notes across topics
- **Topic closed handling**: Graceful error handling for closed topics
- **General chat support**: Works in both general and topic chats

## 🚀 Installation

### Prerequisites
- Python 3.7 or higher
- A Telegram Bot Token (get from [@BotFather](https://t.me/botfather))

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/remindo-bot.git
   cd remindo-bot
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure the bot**
   - Open `Remindo_robot/bot.py`
   - Replace the `TOKEN` variable with your bot token:
     ```python
     TOKEN = "your_bot_token_here"
     ```

4. **Run the bot**
   ```bash
   cd Remindo_robot
   python bot.py
   ```

## 📖 Usage

### 🚀 Quick Start

**For Reminders:**
1. Send `/remind` to start creating a reminder interactively
2. Or use direct format: `/remind 9:00 Take medicine`
3. Use `/list` to see your reminders

**For Notes:**
1. Reply to any message with `/note` to save it
2. Use `/notes` to view your saved notes
3. Add titles: Reply with `/note Meeting Summary`

**For Groups:**
- Reminders and notes are automatically organized by forum topics
- Admins can use `/adminlist` to manage all group reminders
- Set your timezone in private chat with `/settimezone` - it applies to all groups

### Basic Commands
- `/start` - Start the bot and see available commands
- `/help` - Show help information
- `/remind` - Create a new reminder
- `/list` - View your reminders in current topic
- `/list all` - View all your reminders in the group
- `/delete <id>` - Delete a specific reminder
- `/edit <id>` - Edit an existing reminder
- `/settimezone` - Set your timezone (use in private chat)

### Note-Taking Commands
- `/note` - Save a message as a note (reply to message)
- `/notes` - List your notes in current topic
- `/notes all` - List all your notes in the group
- `/deletenote <id>` - Delete a specific note
- `/editnote <id>` - Edit a note's title

### Admin Commands (Group Admins Only)
- `/adminlist` - View all reminders in the group
- `/adminlist all` - View all reminders from all topics
- `/admindelete <id>` - Delete any reminder in the group

### Creating Reminders

#### One-time Reminders
```
/remind 9:00 Take medicine
/remind 2:30 PM Meeting with team
/remind 2024-01-15 14:30 Important deadline
```

#### Recurring Reminders
```
/remind every day at 8:00 Morning routine
/remind every week on monday at 9:00 Weekly meeting
```

### Interactive Creation
1. Send `/remind` without arguments
2. Choose between one-time or recurring reminder
3. Select date (for one-time) or weekdays (for recurring)
4. Enter time
5. Enter your reminder message

### Taking Notes

#### Saving Messages as Notes
- Reply to any message with `/note` to save it
- Reply to any message with `/note Important Info` to save with a title

#### Managing Notes
```
/notes                    # View notes in current topic
/notes all               # View all notes in the group
/deletenote 42           # Delete note with ID 42
/editnote 42             # Edit title of note 42
```

#### Note Features
- **Automatic organization**: Notes are organized by forum topics
- **Rich metadata**: Includes message links, timestamps, and titles
- **Easy access**: Click message links to jump to original messages
- **Topic-aware**: Works seamlessly with forum group topics

## 🛠️ Configuration

### Timezone Setup
- Use `/settimezone` in private chat with the bot to set your personal timezone
- Each user has their own timezone preference that applies to all groups
- Supports 39 different timezone options
- Your timezone setting affects when you receive reminders

### Forum Groups
- The bot automatically detects forum groups
- Reminders and notes are organized by topics
- Use `/list` to see reminders in current topic
- Use `/list all` to see all reminders in the group
- Use `/notes` to see notes in current topic
- Use `/notes all` to see all notes in the group
- Message links include topic parameters for proper navigation

### Admin Features
- **Group management**: Admins can view and delete any reminder in the group
- **Topic support**: Admin commands work with forum group topics
- **Permission checking**: Only group administrators can use admin commands
- **Anonymous protection**: Admin commands are protected from "Send as Group" abuse

## 🔧 Technical Details

### Dependencies
- `python-telegram-bot==20.7` - Telegram Bot API wrapper
- `APScheduler==3.10.4` - Job scheduling
- `dateparser==1.2.0` - Date/time parsing

### Database
- **Dual SQLite databases**: Separate databases for reminders and notes
- **Automatic initialization**: Both databases initialize automatically
- **Rich data storage**: Supports recurring reminders, user timezone preferences, and note metadata
- **Relational structure**: Proper indexing for efficient queries

### Error Handling
- Retry mechanism with exponential backoff
- Topic closed error handling
- Anonymous message protection
- Permission checking for admin-only features

## 🎯 Use Cases

**Remindo Bot** is perfect for:

### 📚 Study Groups
- **Organize assignments**: Use reminders for homework deadlines and exam dates
- **Save lecture notes**: Reply to important messages with `/note` to save key information
- **Topic-based organization**: Separate notes and reminders by subject in forum topics
- **Group coordination**: Admins can manage all reminders across the study group

### 💼 Work Teams
- **Meeting reminders**: Set recurring weekly team meetings
- **Document decisions**: Save important chat messages as notes for future reference
- **Project deadlines**: Track milestones with one-time reminders
- **Knowledge base**: Build a searchable collection of saved information by topic

### 🏘️ Communities
- **Event planning**: Remind members of upcoming community events
- **Information archive**: Save announcements and important messages
- **Multi-topic management**: Organize different community aspects in separate forum topics
- **Admin oversight**: Community managers can oversee all reminders and notes

### 👤 Personal Use
- **Daily routines**: Set recurring reminders for personal habits
- **Information collection**: Save useful messages and links for later reference
- **Cross-platform sync**: Access your reminders and notes from any device with Telegram
- **Smart organization**: Automatic timezone handling and topic organization

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

If you encounter any issues or have questions:
- Create an issue on GitHub
- Contact the bot developer

## 🔒 Security

- Bot tokens are stored in the code (consider using environment variables for production)
- Database files are excluded from version control
- User permissions are properly checked for admin-only features

---

**Made with ❤️ for the Telegram community**

*Transform your Telegram groups into organized productivity hubs with intelligent reminders and seamless note-taking!* 