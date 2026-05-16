"""
Run this script to generate a new ADMIN_KEY and ADMIN_PASSWORD_HASH.

Usage:
    python generate_admin_hash.py

Then:
  - Store ADMIN_KEY in Doppler (external secrets manager, outside Render region)
  - Store ADMIN_PASSWORD_HASH in Render environment variables
"""

import getpass
import hashlib
import hmac
import secrets


def main() -> None:
    password = getpass.getpass("Enter your admin password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.")
        return

    key = secrets.token_hex(32)
    hash_val = hmac.new(
        key.encode("utf-8"),
        password.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    print("\n=== Store in Doppler (external — outside Render region) ===")
    print(f"ADMIN_KEY={key}")
    print("\n=== Store in Render environment variables ===")
    print(f"ADMIN_PASSWORD_HASH={hash_val}")
    print("\nThe password itself is never stored anywhere.")


if __name__ == "__main__":
    main()
