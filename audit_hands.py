"""Deep audit: check if 4P BB push EVER happened recently."""
import sqlite3

HERO_UID = "13268363"
conn = sqlite3.connect('d:/aof_bot/automation/data/hands.db')

out = []

# Recent 4P BB hero actions
out.append("=== ALL 4P BB HERO ACTIONS (recent 500 hands) ===")
rows = conn.execute("""
    SELECT hp.hand_id, h.timestamp, hp.position, hp.action, hp.cards, hp.prior_actions, hp.profit_bb
    FROM hand_players hp
    JOIN hands h ON hp.hand_id = h.id
    WHERE hp.player_id = ? AND h.num_players = 4 AND hp.position = 'BB'
    ORDER BY hp.hand_id DESC LIMIT 500
""", (HERO_UID,)).fetchall()

push_count = 0
fold_count = 0
other_count = 0
for r in rows:
    hid, ts, pos, action, cards, prior, pbb = r
    act_str = "PUSH" if action == "A" else "FOLD" if action == "F" else action or "?"
    if action == "A": push_count += 1
    elif action == "F": fold_count += 1
    else: other_count += 1
    out.append(f"  #{hid} {ts[:19]} | 4P BB prior={prior or '':>7} | cards={cards or '????':>6} | "
               f"Hero={act_str:>4} | P/L={pbb:>+6.1f}BB")

out.append(f"\nSUMMARY (last {len(rows)} hands as 4P BB):")
out.append(f"  PUSH: {push_count}")
out.append(f"  FOLD: {fold_count}")
out.append(f"  OTHER: {other_count}")
if push_count + fold_count > 0:
    out.append(f"  Push%: {push_count/(push_count+fold_count)*100:.1f}%")

# Also check 2P BB for comparison
out.append("\n\n=== 2P BB HERO ACTIONS (last 200) ===")
rows2 = conn.execute("""
    SELECT hp.hand_id, hp.action, hp.cards, hp.prior_actions, hp.profit_bb
    FROM hand_players hp
    JOIN hands h ON hp.hand_id = h.id
    WHERE hp.player_id = ? AND h.num_players = 2 AND hp.position = 'BB'
    ORDER BY hp.hand_id DESC LIMIT 200
""", (HERO_UID,)).fetchall()

p2, f2, o2 = 0, 0, 0
for r in rows2:
    if r[1] == "A": p2 += 1
    elif r[1] == "F": f2 += 1
    else: o2 += 1

out.append(f"  PUSH: {p2}, FOLD: {f2}, OTHER: {o2}")
if p2 + f2 > 0:
    out.append(f"  Push%: {p2/(p2+f2)*100:.1f}%")

# check 4P other positions
for pos_name in ["CO", "BTN", "SB"]:
    rows_pos = conn.execute("""
        SELECT hp.action, COUNT(*) FROM hand_players hp
        JOIN hands h ON hp.hand_id = h.id
        WHERE hp.player_id = ? AND h.num_players = 4 AND hp.position = ?
        GROUP BY hp.action
    """, (HERO_UID, pos_name)).fetchall()
    out.append(f"\n4P {pos_name}: {dict(rows_pos)}")

# Also check if cards are being stored for pushed hands at 4P BB
out.append("\n\n=== 4P BB PUSHES with cards (all time) ===")
rows3 = conn.execute("""
    SELECT hp.hand_id, h.timestamp, hp.cards, hp.prior_actions, hp.profit_bb
    FROM hand_players hp
    JOIN hands h ON hp.hand_id = h.id
    WHERE hp.player_id = ? AND h.num_players = 4 AND hp.position = 'BB' AND hp.action = 'A'
    ORDER BY hp.hand_id DESC LIMIT 50
""", (HERO_UID,)).fetchall()

for r in rows3:
    out.append(f"  #{r[0]} {r[1][:19]} | cards={r[2] or '????'} | prior={r[3]} | P/L={r[4]:>+.1f}BB")
out.append(f"  Total 4P BB pushes found: {len(rows3)}")

conn.close()

result = "\n".join(out)
with open("d:/aof_bot/audit_4p_bb.txt", "w", encoding="utf-8") as f:
    f.write(result)
print(f"Written to audit_4p_bb.txt ({len(result)} bytes)")
print(f"\n4P BB: PUSH={push_count} FOLD={fold_count} OTHER={other_count}")
print(f"2P BB: PUSH={p2} FOLD={f2} OTHER={o2}")
