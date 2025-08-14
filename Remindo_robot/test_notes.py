#!/usr/bin/env python3
"""
Test script for note-taking features
This script tests the notes database functions
"""

import notes_db
import sys

def test_notes_functions():
    """Test the notes database functions"""
    print("🧪 Testing Note-Taking Features...")
    
    # Initialize database
    notes_db.init_notes_db()
    
    # Test data
    test_user_id = 123456789
    test_chat_id = -1001234567890
    test_message_id = 987654321
    test_message_text = "This is a test message for note taking"
    test_message_link = "https://t.me/test/987654321"
    test_topic_id = 5
    
    print("1. Testing add_note...")
    note_id = notes_db.add_note(
        user_id=test_user_id,
        chat_id=test_chat_id,
        message_id=test_message_id,
        message_text=test_message_text,
        message_link=test_message_link,
        topic_id=test_topic_id,
        title="Test Note",
        description="This is a test note description"
    )
    print(f"   ✅ Added note with ID: {note_id}")
    
    print("2. Testing get_user_notes...")
    notes = notes_db.get_user_notes(test_user_id, test_chat_id, test_topic_id)
    print(f"   ✅ Found {len(notes)} notes in topic")
    
    print("3. Testing get_all_user_notes_in_chat...")
    all_notes = notes_db.get_all_user_notes_in_chat(test_user_id, test_chat_id)
    print(f"   ✅ Found {len(all_notes)} notes in chat")
    
    print("4. Testing get_note_by_id...")
    note = notes_db.get_note_by_id(note_id, test_user_id)
    if note:
        print(f"   ✅ Found note: {note[5]}")  # message_text
    else:
        print("   ❌ Failed to find note")
        return False
    
    print("5. Testing update_note...")
    success = notes_db.update_note(note_id, test_user_id, title="Updated Test Note", description="Updated description")
    if success:
        print("   ✅ Successfully updated note")
    else:
        print("   ❌ Failed to update note")
        return False
    
    print("6. Testing search_notes...")
    search_results = notes_db.search_notes(test_user_id, test_chat_id, "test", test_topic_id)
    print(f"   ✅ Found {len(search_results)} notes in search")
    
    print("7. Testing get_note_count...")
    count = notes_db.get_note_count(test_user_id, test_chat_id, test_topic_id)
    print(f"   ✅ Note count: {count}")
    
    print("8. Testing delete_note...")
    success = notes_db.delete_note(note_id, test_user_id)
    if success:
        print("   ✅ Successfully deleted note")
    else:
        print("   ❌ Failed to delete note")
        return False
    
    print("9. Verifying deletion...")
    note = notes_db.get_note_by_id(note_id, test_user_id)
    if not note:
        print("   ✅ Note successfully deleted")
    else:
        print("   ❌ Note still exists after deletion")
        return False
    
    print("\n🎉 All note-taking feature tests passed!")
    return True

if __name__ == "__main__":
    success = test_notes_functions()
    sys.exit(0 if success else 1) 