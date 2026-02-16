"""
Test script to verify OpenAI API key is working correctly.
Tests both environment variable access and actual API calls.
Test after running in bash: export OPENAI_API_KEY="sk-your-actual-key-here"
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

print("=" * 60)
print("OpenAI API Key Test Script")
print("=" * 60)

# Test 1: Check script directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
print(f"\n1. Script directory: {SCRIPT_DIR}")

# Test 2: Check for .env file
env_file_path = os.path.join(SCRIPT_DIR, ".env")
env_exists = os.path.exists(env_file_path)
print(f"\n2. .env file exists: {env_exists}")
if env_exists:
    print(f"   Path: {env_file_path}")
    # Show content (masked)
    with open(env_file_path, 'r') as f:
        lines = f.readlines()
    print(f"   Lines in .env: {len(lines)}")
    for line in lines:
        if line.strip() and not line.startswith('#'):
            key = line.split('=')[0] if '=' in line else line
            print(f"     - {key.strip()}=***")

# Test 3: Check environment variable BEFORE load_dotenv
api_key_before = os.environ.get("OPENAI_API_KEY")
print(f"\n3. OPENAI_API_KEY in environment (before load_dotenv):")
if api_key_before:
    print(f"   Found: sk-...{api_key_before[-4:]}")
else:
    print("   Not found")

# Test 4: Load .env with override=False (like classify.py does)
if env_exists:
    print(f"\n4. Loading .env with override=False...")
    load_dotenv(env_file_path, override=False)
    api_key_after_no_override = os.environ.get("OPENAI_API_KEY")
    if api_key_after_no_override:
        print(f"   Found: sk-...{api_key_after_no_override[-4:]}")
    else:
        print("   Not found")

# Test 5: Load .env with override=True (to see if it makes a difference)
if env_exists:
    print(f"\n5. Loading .env with override=True...")
    load_dotenv(env_file_path, override=True)
    api_key_after_override = os.environ.get("OPENAI_API_KEY")
    if api_key_after_override:
        print(f"   Found: sk-...{api_key_after_override[-4:]}")
    else:
        print("   Not found")

# Test 6: Check what classify.py sees
print(f"\n6. Testing classify.py import...")
try:
    # Reset the environment to simulate fresh import
    import classify
    print("   ✓ classify.py imported successfully")
    
    # Test the _get_client function
    try:
        client, model = classify._get_client()
        print(f"   ✓ OpenAI client initialized")
        print(f"   ✓ Model: {model}")
    except Exception as e:
        print(f"   ✗ Error getting client: {e}")
        sys.exit(1)
        
except Exception as e:
    print(f"   ✗ Error importing classify.py: {e}")
    sys.exit(1)

# Test 7: Make a real API call
print(f"\n7. Testing actual API call...")
try:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": "Say 'API key is working!' and nothing else."}
        ],
        temperature=0,
        max_tokens=10,
    )
    result = response.choices[0].message.content.strip()
    print(f"   ✓ API call successful!")
    print(f"   Response: {result}")
except Exception as e:
    print(f"   ✗ API call failed: {e}")
    sys.exit(1)

print("\n" + "=" * 60)
print("✓ ALL TESTS PASSED - API key is working correctly!")
print("=" * 60)
