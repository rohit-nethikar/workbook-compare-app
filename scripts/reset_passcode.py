import sqlite3

DB_PATH = r"C:\Users\m239012\OneDrive - Mayo Clinic\GitHub_claude\workbook-compare-app\instance\cache.db"

conn = sqlite3.connect(DB_PATH)
conn.execute("DELETE FROM app_config WHERE key = ?", ("passcode_hash",))
conn.commit()
conn.close()
print("passcode cleared")
