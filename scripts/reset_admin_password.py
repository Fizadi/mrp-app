import hashlib
import secrets
import sqlite3

DB_PATH = r"D:\Logistics Project, 2024-02-01\MRP Project\FlaskApp\Instances\MRP_database.db"

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{pwd_hash}"

def reset_password(username='admin', new_password='admin123'):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Check if user exists
    cursor.execute("SELECT UserID FROM t_Users WHERE Username = ?", (username,))
    user = cursor.fetchone()
    if user:
        new_hash = hash_password(new_password)
        cursor.execute("UPDATE t_Users SET PasswordHash = ?, MustChangePassword = 1 WHERE Username = ?", (new_hash, username))
        # Also add to password history (optional)
        cursor.execute("INSERT INTO t_PasswordHistory (UserID, PasswordHash, ChangedDate) VALUES (?, ?, CURRENT_TIMESTAMP)", (user[0], new_hash))
        conn.commit()
        print(f"Password for '{username}' reset to '{new_password}'")
    else:
        print(f"User '{username}' not found")
    conn.close()

if __name__ == "__main__":
    reset_password()