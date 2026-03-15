import sys
import sqlite3
import json
from pathlib import Path

# Add automation/hook dirs
sys.path.insert(0, str(Path("d:/aof_bot/automation")))
sys.path.insert(0, str(Path("d:/aof_bot/hook")))

import packet_capture
from packet_capture import PacketCapture

# Mock save_packet to prevent infinite growth
packet_capture.save_packet = lambda name, table_id, pkt, db_path=None: None
# Mock API calls to speed up rebuild
packet_capture.PacketCapture._send_hand_to_api = lambda self, pids, actions, cards, hs: None
packet_capture.PacketCapture._auto_click_leave = lambda self: None
packet_capture.PacketCapture._auto_click_enter = lambda self, a, b, c: None
packet_capture.PacketCapture._check_auto_exit = lambda self, a, b: None
packet_capture.PacketCapture._execute_action = lambda self, a: None

# Speed up SQLite inserts using a single global connection
global_conn = sqlite3.connect("d:/aof_bot/automation/data/hands.db", isolation_level=None)
global_conn.execute("PRAGMA synchronous = OFF")
global_conn.execute("PRAGMA journal_mode = MEMORY")
global_conn.execute("BEGIN TRANSACTION")

class FastConn:
    def __init__(self, conn):
        self.conn = conn
    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)
    def commit(self):
        pass
    def close(self):
        pass

orig_connect = sqlite3.connect
# Monkey-patch sqlite3.connect inside packet_capture module
packet_capture.sqlite3.connect = lambda *args, **kwargs: FastConn(global_conn)

def rebuild():
    print("Rebuilding hands.db from packets.db (Fast Mode)...")
    # Clear hands.db entirely
    hands_db_path = Path("d:/aof_bot/automation/data/hands.db")
    
    global_conn.execute("DROP TABLE IF EXISTS hands")
    global_conn.execute("DROP TABLE IF EXISTS hand_players")
    global_conn.execute("DROP TABLE IF EXISTS player_stats")
    global_conn.execute("DROP TABLE IF EXISTS ofc_hands")
    
    # Re-init db via our fast connection
    packet_capture.init_hands_db(hands_db_path)
    
    # Initialize fake capture
    cap = PacketCapture(verbose=False, enable_solver=False, auto_play=False)
    
    import os
    sys.stdout = open(os.devnull, 'w')
    
    # Fetch all packets
    packets_db = Path("d:/aof_bot/automation/data/packets.db")
    pconn = orig_connect(str(packets_db))
    pcursor = pconn.cursor()
    pcursor.execute("SELECT timestamp, packet_type, table_id, data FROM packets ORDER BY id ASC")
    
    count = 0
    while True:
        rows = pcursor.fetchmany(10000)
        if not rows: break
        
        for r in rows:
            ts, name, table_id, data_str = r
            try:
                pkt = json.loads(data_str)
                payload = {"name": name, "tableId": table_id, "data": pkt}
                cap._handle_packet(payload)
                count += 1
            except Exception as e:
                pass
                
    pconn.close()
    
    sys.stdout = sys.__stdout__
    print(f"Done processing {count} packets.")
    print(f"Saved hands: {cap.hands_saved}")

if __name__ == "__main__":
    rebuild()
    global_conn.execute("COMMIT")
    global_conn.close()
