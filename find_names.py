import re
import os

LOG_FILE = "d:/aof_bot/capture.log"

top_uids = ["13268363", "13323436", "13386305", "3971287", "13386498", "13276158"]

uid_to_name = {}

if os.path.exists(LOG_FILE):
    # Regex to match: [SitDown] Seat X: NAME_HERE (uid=13323436)
    # Or: OtherEnter NAME (uid=XXX) joined table
    pattern_sit = re.compile(r"Seat \d+: (.+) \(uid=(\d+)\)")
    pattern_enter = re.compile(r"OtherEnter\] (.+) \(uid=(\d+)\)")
    
    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m1 = pattern_sit.search(line)
            if m1:
                name, uid = m1.groups()
                if uid in top_uids:
                    uid_to_name[uid] = name
                    
            m2 = pattern_enter.search(line)
            if m2:
                name, uid = m2.groups()
                if uid in top_uids:
                    uid_to_name[uid] = name

    print("--- Found Names ---")
    for uid in top_uids:
        name = uid_to_name.get(uid, "Unknown")
        print(f"UID: {uid} -> Name: {name}")
else:
    print(f"Log file {LOG_FILE} not found.")
