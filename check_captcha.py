"""Check ALL CAPTCHA packets including the latest slider type."""
import sqlite3, json

conn = sqlite3.connect('d:/aof_bot/automation/data/packets.db')
out = []

out.append("=== ALL CAPTCHA PACKETS ===\n")
rows = conn.execute(
    "SELECT timestamp, packet_type, data FROM packets "
    "WHERE packet_type IN ('ShowCaptchaRSP','CaptchaRSP') "
    "ORDER BY timestamp"
).fetchall()

for r in rows:
    out.append(f"--- {r[0]} | {r[1]} ---")
    try:
        parsed = json.loads(r[2])
        # Print without rawHex for readability
        clean = {k:v for k,v in parsed.items() if k != '_rawHex'}
        out.append(f"  Fields: {json.dumps(clean, indent=2, ensure_ascii=False)}")
        if '_rawHex' in parsed:
            # Analyze raw hex to show int32 values at each 4-byte offset
            hex_str = parsed['_rawHex']
            hex_bytes = bytes.fromhex(hex_str.replace(' ', ''))
            out.append(f"  Raw hex length: {len(hex_bytes)} bytes")
            out.append(f"  Key offsets:")
            for offset in [0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28, 0x2C, 0x30, 0x34, 0x38, 0x3C, 0x40]:
                if offset + 4 <= len(hex_bytes):
                    val = int.from_bytes(hex_bytes[offset:offset+4], 'little', signed=True)
                    out.append(f"    0x{offset:02X}: {val} (0x{val & 0xFFFFFFFF:08X})")
    except Exception as e:
        out.append(f"  Raw: {r[2]}")
        out.append(f"  Error: {e}")
    out.append("")

# Check for CAPTCHA events today (2026-03-28)
out.append("\n=== CAPTCHA CONTEXT: Packets around 10:55 (screenshot time) ===")
rows2 = conn.execute(
    "SELECT timestamp, packet_type, substr(data,1,300) FROM packets "
    "WHERE timestamp BETWEEN '2026-03-28T10:54:00' AND '2026-03-28T10:57:00' "
    "ORDER BY timestamp"
).fetchall()
for r in rows2:
    out.append(f"  {r[0]} | {r[1]:30s} | {r[2]}")

out.append("\n=== CAPTCHA CONTEXT: Packets around 13:00-13:26 (current session) ===")
rows3 = conn.execute(
    "SELECT timestamp, packet_type, substr(data,1,300) FROM packets "
    "WHERE packet_type IN ('ShowCaptchaRSP','CaptchaRSP') "
    "AND timestamp > '2026-03-28T10:00:00' "
    "ORDER BY timestamp"
).fetchall()
for r in rows3:
    out.append(f"  {r[0]} | {r[1]:30s} | {r[2]}")

conn.close()

result = "\n".join(out)
with open("d:/aof_bot/captcha_log.txt", "w", encoding="utf-8") as f:
    f.write(result)
print(f"Written {len(result)} bytes, {len(rows)} total CAPTCHA packets")
