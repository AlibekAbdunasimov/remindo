# 🤖 Remindo Bot

A powerful Telegram reminder bot with support for one-time and recurring reminders, timezone management, and forum group integration.

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

### ⚙️ Settings & Configuration
- **Timezone support**: `/settimezone` - Set timezone for users/groups
- **Admin-only timezone**: Group admins can set group timezone
- **Timezone persistence**: Stored in database for each user/group
- **UTC offset selection**: 39 different timezone options

### 🏛️ Forum Group Support
- **Topic-aware reminders**: Reminders automatically associated with topics
- **Topic-specific listing**: View reminders by topic
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

### Basic Commands
- `/start` - Start the bot and see available commands
- `/help` - Show help information
- `/remind` - Create a new reminder
- `/list` - View your reminders in current topic
- `/list all` - View all your reminders in the group
- `/delete <id>` - Delete a specific reminder
- `/edit <id>` - Edit an existing reminder
- `/settimezone` - Set your timezone

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

## 🛠️ Configuration

### Timezone Setup
- Use `/settimezone` to set your timezone
- In groups, only admins can set the group timezone
- Supports 39 different timezone options

### Forum Groups
- The bot automatically detects forum groups
- Reminders are organized by topics
- Use `/list` to see reminders in current topic
- Use `/list all` to see all reminders in the group

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
- SQLite database for storing reminders and preferences
- Automatic database initialization
- Supports recurring reminders and timezone preferences

### Error Handling
- Retry mechanism with exponential backoff
- Topic closed error handling
- Anonymous message protection
- Permission checking for admin-only features

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