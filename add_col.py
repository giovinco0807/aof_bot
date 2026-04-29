import sqlite3

def add_col():
    conn = sqlite3.connect('d:/aof_bot/automation/data/hands.db')
    try:
        conn.execute('ALTER TABLE player_stats ADD COLUMN player_name TEXT DEFAULT ""')
        conn.commit()
        print('Added player_name column successfully.')
    except Exception as e:
        print(f"Error (maybe already exists?): {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    add_col()
