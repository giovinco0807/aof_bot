"""Supabase cloud database wrapper for hand history sharing across multiple PCs.

Uses the Supabase REST API (PostgREST) directly via `requests` — no extra
dependencies required beyond what's already installed.

Usage:
    from cloud_db import CloudDB
    db = CloudDB()  # reads config from cloud_config.json
    db.save_hand(record)
    db.update_player_stats(pid, pushed, profit_chips, profit_bb, card, now)
"""

import json
import os
import socket
import requests
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "data" / "cloud_config.json"

# Default config template
DEFAULT_CONFIG = {
    "supabase_url": "",
    "supabase_anon_key": "",
    "pc_name": socket.gethostname(),  # auto-detect PC name
    "enabled": True,
}


class CloudDB:
    """Non-blocking Supabase REST API wrapper. All methods catch exceptions
    silently so cloud failures never affect the local bot operation."""

    def __init__(self, config_path: Path = CONFIG_PATH):
        self.config = dict(DEFAULT_CONFIG)
        self.enabled = False
        self._base_url = ""
        self._headers = {}

        try:
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    self.config.update(json.load(f))
            else:
                # Write default config for user to fill in
                config_path.parent.mkdir(parents=True, exist_ok=True)
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
                print(f"  [Cloud] Created config template: {config_path}")
                print(f"  [Cloud] Please fill in supabase_url and supabase_anon_key")
                return

            url = self.config.get("supabase_url", "").rstrip("/")
            key = self.config.get("supabase_anon_key", "")

            if not url or not key:
                print("  [Cloud] Supabase URL or key not configured — cloud sync disabled")
                return

            self._base_url = f"{url}/rest/v1"
            self._headers = {
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }
            self.enabled = self.config.get("enabled", True)
            pc = self.config.get("pc_name", "unknown")
            print(f"  [Cloud] Connected to Supabase ({pc}) — cloud sync enabled")

        except Exception as e:
            print(f"  [Cloud] Init error: {e}")

    def save_hand(self, record: dict) -> int:
        """Save a completed hand + per-player records to cloud.
        Returns the cloud hand_id or -1 on failure."""
        if not self.enabled:
            return -1

        try:
            # 1. Insert into hands table
            hand_data = {
                "timestamp": record["timestamp"],
                "table_id": record.get("table_id", ""),
                "num_players": record["num_players"],
                "bb_size": record["bb_size"],
                "dealer_seat": record.get("dealer_seat", -1),
                "player_ids": record["player_ids"],
                "stacks": record.get("stacks", ""),
                "actions": record["actions"],
                "cards": record.get("cards", ""),
                "board": record.get("board", ""),
                "winner_seat": record.get("winner_seat", -1),
                "pot_chips": record.get("pot_chips", 0),
                "rake_chips": record.get("rake_chips", 0),
                "pc_name": self.config.get("pc_name", "unknown"),
            }

            resp = requests.post(
                f"{self._base_url}/hands",
                headers=self._headers,
                json=hand_data,
                timeout=5,
            )
            if resp.status_code not in (200, 201):
                print(f"  [Cloud] hands INSERT failed: {resp.status_code} {resp.text[:200]}")
                return -1

            result = resp.json()
            hand_id = result[0]["id"] if result else -1

            # 2. Insert hand_players
            pids = record["player_ids"].split(",")
            actions = record["actions"].split(",")
            cards_list = record.get("cards", "").split(",") if record.get("cards") else []
            profits = record.get("profits", {})
            positions = record.get("positions", [])
            bb_size = record["bb_size"]

            players_data = []
            for i, (pid, action) in enumerate(zip(pids, actions)):
                pid = pid.strip()
                if not pid:
                    continue
                action = action.strip().upper()
                card = cards_list[i].strip() if i < len(cards_list) else ""
                seat_id = int(record.get("seat_ids", [i])[i]) if "seat_ids" in record else i
                position = positions[i] if i < len(positions) else ""
                profit_chips = profits.get(str(seat_id), 0)
                profit_bb = round(profit_chips / bb_size, 2) if bb_size > 0 else 0

                players_data.append({
                    "hand_id": hand_id,
                    "player_id": pid,
                    "seat_id": seat_id,
                    "position": position,
                    "action": action,
                    "cards": card,
                    "profit_chips": profit_chips,
                    "profit_bb": profit_bb,
                })

            if players_data:
                resp2 = requests.post(
                    f"{self._base_url}/hand_players",
                    headers=self._headers,
                    json=players_data,
                    timeout=5,
                )
                if resp2.status_code not in (200, 201):
                    print(f"  [Cloud] hand_players INSERT failed: {resp2.status_code}")

            # 3. Update player_stats via upsert
            for pd in players_data:
                self._upsert_player_stats(pd, record["timestamp"])

            print(f"  [Cloud] Hand #{hand_id} synced to cloud")
            return hand_id

        except requests.RequestException as e:
            print(f"  [Cloud] Network error: {e}")
            return -1
        except Exception as e:
            print(f"  [Cloud] save_hand error: {e}")
            return -1

    def _upsert_player_stats(self, pd: dict, timestamp: str):
        """Upsert player stats using Supabase's built-in upsert."""
        try:
            pid = pd["player_id"]
            pushed = 1 if pd["action"] == "A" else 0
            has_card = 1 if pd.get("cards") else 0

            # Try to get existing stats first
            resp = requests.get(
                f"{self._base_url}/player_stats?player_id=eq.{pid}",
                headers=self._headers,
                timeout=3,
            )

            if resp.status_code == 200 and resp.json():
                # Update existing
                existing = resp.json()[0]
                update_data = {
                    "hands_seen": existing["hands_seen"] + 1,
                    "hands_pushed": existing["hands_pushed"] + pushed,
                    "total_profit_chips": existing["total_profit_chips"] + pd["profit_chips"],
                    "total_profit_bb": existing["total_profit_bb"] + pd["profit_bb"],
                    "showdown_count": existing["showdown_count"] + has_card,
                    "last_seen": timestamp,
                }
                # Append showdown hand
                if pd.get("cards"):
                    hands = existing.get("showdown_hands", []) or []
                    if pd["cards"] not in hands[-20:]:
                        hands.append(pd["cards"])
                    update_data["showdown_hands"] = hands

                requests.patch(
                    f"{self._base_url}/player_stats?player_id=eq.{pid}",
                    headers=self._headers,
                    json=update_data,
                    timeout=3,
                )
            else:
                # Insert new
                new_data = {
                    "player_id": pid,
                    "hands_seen": 1,
                    "hands_pushed": pushed,
                    "total_profit_chips": pd["profit_chips"],
                    "total_profit_bb": pd["profit_bb"],
                    "showdown_count": has_card,
                    "showdown_hands": [pd["cards"]] if pd.get("cards") else [],
                    "last_seen": timestamp,
                }
                requests.post(
                    f"{self._base_url}/player_stats",
                    headers=self._headers,
                    json=new_data,
                    timeout=3,
                )
        except Exception:
            pass  # Silent fail for stats update

    def get_player_stats(self, player_id: str) -> dict:
        """Get aggregated player stats from cloud."""
        if not self.enabled:
            return {}
        try:
            resp = requests.get(
                f"{self._base_url}/player_stats?player_id=eq.{player_id}",
                headers=self._headers,
                timeout=3,
            )
            if resp.status_code == 200 and resp.json():
                return resp.json()[0]
        except Exception:
            pass
        return {}

    def test_connection(self) -> bool:
        """Test the Supabase connection."""
        if not self.enabled:
            return False
        try:
            resp = requests.get(
                f"{self._base_url}/hands?limit=1",
                headers=self._headers,
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False
