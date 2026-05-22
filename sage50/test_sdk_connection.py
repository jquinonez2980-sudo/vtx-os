import mysql.connector

try:
    conn = mysql.connector.connect(
        host="localhost",
        port=13531,
        user="admin",
        password=""
    )
    cursor = conn.cursor()
    cursor.execute("SHOW DATABASES")
    for db in cursor.fetchall():
        print(db)
    conn.close()
    print("\nConnection successful!")
except Exception as e:
    print(f"Error: {e}")