#!/usr/bin/env python3
"""
Test script for admin features
This script tests the admin database functions
"""

import db
import sys

def test_admin_functions():
    """Test the admin database functions"""
    print("🧪 Testing Admin Features...")
    
    # Initialize database
    db.init_db()
    
    # Test data
    test_chat_id = -1001234567890
    test_user_id = 123456789
    test_message = "Test reminder"
    test_time = "2024-01-15T10:00:00+00:00"
    test_timezone = "UTC"
    
    print("1. Testing add_reminder...")
    reminder_id = db.add_reminder(
        test_user_id, 
        test_chat_id, 
        test_message, 
        test_time, 
        test_timezone
    )
    print(f"   ✅ Added reminder with ID: {reminder_id}")
    
    print("2. Testing get_all_group_reminders...")
    reminders = db.get_all_group_reminders(test_chat_id)
    print(f"   ✅ Found {len(reminders)} reminders in group")
    
    print("3. Testing get_reminder_by_id_admin...")
    reminder = db.get_reminder_by_id_admin(reminder_id, test_chat_id)
    if reminder:
        print(f"   ✅ Found reminder: {reminder[1]}")  # message
    else:
        print("   ❌ Failed to find reminder")
        return False
    
    print("4. Testing admin_delete_reminder...")
    success = db.admin_delete_reminder(reminder_id, test_chat_id)
    if success:
        print("   ✅ Successfully deleted reminder")
    else:
        print("   ❌ Failed to delete reminder")
        return False
    
    print("5. Verifying deletion...")
    reminder = db.get_reminder_by_id_admin(reminder_id, test_chat_id)
    if not reminder:
        print("   ✅ Reminder successfully deleted")
    else:
        print("   ❌ Reminder still exists after deletion")
        return False
    
    print("\n🎉 All admin feature tests passed!")
    return True

if __name__ == "__main__":
    success = test_admin_functions()
    sys.exit(0 if success else 1) 