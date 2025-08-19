# Database Migration: SQLite to PostgreSQL

This guide explains how to migrate your Remindo Bot from SQLite to PostgreSQL.

## Why PostgreSQL?

PostgreSQL offers several advantages over SQLite for production deployments:

- **Better concurrency**: Handle multiple users simultaneously
- **Advanced data types**: Better timezone support, JSON columns, etc.
- **Scalability**: Better performance with large datasets
- **Production ready**: Suitable for cloud deployments
- **Advanced features**: Full-text search, advanced indexing, etc.

## Prerequisites

### 1. Install PostgreSQL

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
```

**macOS (using Homebrew):**
```bash
brew install postgresql
brew services start postgresql
```

**Windows:**
Download and install from [PostgreSQL official website](https://www.postgresql.org/download/windows/)

### 2. Create Database and User

Connect to PostgreSQL as superuser:
```bash
sudo -u postgres psql
```

Create database and user:
```sql
CREATE DATABASE remindo_bot;
CREATE USER remindo_user WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE remindo_bot TO remindo_user;
ALTER USER remindo_user CREATEDB;
\q
```

### 3. Install Python Dependencies

```bash
pip install -r requirements.txt
```

## Configuration

### 1. Environment Variables

Create a `.env` file in your project root (copy from `env.example`):

```env
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN=your_bot_token_here

# PostgreSQL Database Configuration
DB_HOST=localhost
DB_PORT=5432
DB_NAME=remindo_bot
DB_USER=remindo_user
DB_PASSWORD=your_secure_password

# Alternative: Use DATABASE_URL for hosted databases
# DATABASE_URL=postgresql://user:password@host:port/database
```

### 2. For Cloud Deployments

If using cloud services like Heroku, Railway, or Render, you can use the `DATABASE_URL` environment variable:

```env
DATABASE_URL=postgresql://username:password@hostname:port/database_name
```

## Migration Process

### Option 1: Fresh Installation (No existing data)

If you're setting up a new bot instance:

```bash
python migrate_to_postgresql.py
```

This will create all necessary tables in PostgreSQL.

### Option 2: Migrate from Existing SQLite Data

If you have existing SQLite databases (`reminders.db`, `notes.db`):

1. **Backup your existing databases:**
   ```bash
   cp reminders.db reminders.db.backup
   cp notes.db notes.db.backup
   ```

2. **Run the migration script:**
   ```bash
   python migrate_to_postgresql.py
   ```

3. **Verify the migration:**
   - Check that your reminders and notes are properly migrated
   - Test bot functionality
   - Once confirmed, you can delete the old SQLite files

### Option 3: Manual Migration

If you prefer manual control:

```python
# Set environment variables first
import os
os.environ['DATABASE_URL'] = 'postgresql://user:password@localhost/remindo_bot'

# Initialize database
from Remindo_robot import db, notes_db
db.init_db()
notes_db.init_notes_db()
```

## Key Changes in PostgreSQL Version

### 1. Data Types
- `INTEGER` → `BIGINT` for user_id and chat_id (Telegram IDs are large)
- `INTEGER` → `SERIAL` for auto-incrementing primary keys
- `TEXT` timestamps → `TIMESTAMP WITH TIME ZONE`
- SQLite integers (0/1) → PostgreSQL `BOOLEAN` (TRUE/FALSE)

### 2. Query Syntax
- Parameter placeholders: `?` → `%s`
- Boolean values: `0/1` → `FALSE/TRUE`
- Case-insensitive search: `LIKE` → `ILIKE`
- Upsert syntax: `INSERT OR REPLACE` → `INSERT ... ON CONFLICT`

### 3. Performance Improvements
- Added comprehensive indexes for better query performance
- Optimized connection handling with context managers
- Better error handling and logging

## Verification

After migration, verify everything works:

1. **Start the bot:**
   ```bash
   cd Remindo_robot
   python bot.py
   ```

2. **Test functionality:**
   - Create a reminder: `/remind`
   - List reminders: `/list`
   - Save a note: `/note` (reply to a message)
   - List notes: `/notes`
   - Set timezone: `/settimezone`

3. **Check database:**
   ```bash
   psql -U remindo_user -d remindo_bot -c "SELECT COUNT(*) FROM reminders;"
   psql -U remindo_user -d remindo_bot -c "SELECT COUNT(*) FROM notes;"
   ```

## Troubleshooting

### Connection Issues

1. **Check PostgreSQL is running:**
   ```bash
   sudo systemctl status postgresql  # Linux
   brew services list | grep postgresql  # macOS
   ```

2. **Verify database exists:**
   ```bash
   psql -U remindo_user -d remindo_bot -c "SELECT version();"
   ```

3. **Check environment variables:**
   ```python
   import os
   print(os.getenv('DATABASE_URL'))
   ```

### Migration Issues

1. **Permission errors:**
   ```sql
   GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO remindo_user;
   GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO remindo_user;
   ```

2. **Data type errors:**
   - Check that Telegram IDs fit in BIGINT
   - Verify timestamp formats

3. **Connection limits:**
   - Increase `max_connections` in postgresql.conf if needed

## Cloud Deployment

### Heroku
```bash
heroku addons:create heroku-postgresql:hobby-dev
# DATABASE_URL is automatically set
```

### Railway
```bash
railway add postgresql
# DATABASE_URL is automatically set
```

### Render
- Add PostgreSQL service in dashboard
- Copy DATABASE_URL to environment variables

## Performance Tips

1. **Connection pooling** (for high-traffic bots):
   ```python
   # Consider using connection pooling libraries
   pip install psycopg2-pool
   ```

2. **Database optimization:**
   ```sql
   -- Analyze tables for better query planning
   ANALYZE reminders;
   ANALYZE notes;
   
   -- Check index usage
   SELECT schemaname, tablename, indexname, idx_scan 
   FROM pg_stat_user_indexes 
   ORDER BY idx_scan DESC;
   ```

3. **Monitor performance:**
   ```sql
   -- Check slow queries
   SELECT query, mean_time, calls 
   FROM pg_stat_statements 
   ORDER BY mean_time DESC 
   LIMIT 10;
   ```

## Rollback Plan

If you need to rollback to SQLite:

1. Keep your SQLite backup files
2. Revert the code changes in `db.py` and `notes_db.py`
3. Restore from backup files

## Support

If you encounter issues:

1. Check the logs for detailed error messages
2. Verify your PostgreSQL configuration
3. Test database connectivity independently
4. Check environment variables are set correctly

Remember to never commit your `.env` file or database credentials to version control!
