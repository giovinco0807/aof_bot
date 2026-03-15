//! Hand history database for recording opponent actions and showdown results.
//!
//! Uses SQLite (rusqlite) for persistent local storage.

use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};

/// A recorded hand history entry.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HandRecord {
    /// Timestamp (ISO 8601)
    pub timestamp: String,
    /// Number of players at the table
    pub num_players: u8,
    /// Stack size in BB
    pub stack_bb: f64,
    /// Player IDs at each position
    pub player_ids: Vec<String>,
    /// Actions taken by each player ('F' or 'A')
    pub actions: String,
    /// Cards shown at showdown (if any), indexed by position
    /// Format: "AcKd" or empty if not shown
    pub showdown_cards: Vec<String>,
    /// Pot size in BB
    pub pot_bb: f64,
}

/// Hand history database manager.
pub struct HandHistoryDb {
    conn: Connection,
}

impl HandHistoryDb {
    /// Open or create the database at the given path.
    pub fn open(path: &str) -> Result<Self, String> {
        let conn = Connection::open(path)
            .map_err(|e| format!("Failed to open DB: {}", e))?;

        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS hands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                num_players INTEGER NOT NULL,
                stack_bb REAL NOT NULL,
                player_ids TEXT NOT NULL,
                actions TEXT NOT NULL,
                showdown_cards TEXT NOT NULL,
                pot_bb REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS player_stats (
                player_id TEXT PRIMARY KEY,
                total_hands INTEGER NOT NULL DEFAULT 0,
                allin_count INTEGER NOT NULL DEFAULT 0,
                fold_count INTEGER NOT NULL DEFAULT 0,
                showdown_count INTEGER NOT NULL DEFAULT 0,
                -- Position-specific stats (JSON arrays)
                allin_by_position TEXT NOT NULL DEFAULT '[]',
                hands_by_position TEXT NOT NULL DEFAULT '[]',
                -- Showdown hand records (JSON array of canonical hands)
                showdown_hands TEXT NOT NULL DEFAULT '[]'
            );

            CREATE INDEX IF NOT EXISTS idx_hands_timestamp ON hands(timestamp);
            CREATE INDEX IF NOT EXISTS idx_player_stats_id ON player_stats(player_id);"
        ).map_err(|e| format!("Failed to create tables: {}", e))?;

        Ok(Self { conn })
    }

    /// Record a hand and update player statistics.
    pub fn record_hand(&self, record: &HandRecord) -> Result<i64, String> {
        let player_ids_json = serde_json::to_string(&record.player_ids)
            .map_err(|e| format!("JSON error: {}", e))?;
        let showdown_json = serde_json::to_string(&record.showdown_cards)
            .map_err(|e| format!("JSON error: {}", e))?;

        self.conn.execute(
            "INSERT INTO hands (timestamp, num_players, stack_bb, player_ids, actions, showdown_cards, pot_bb)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                record.timestamp,
                record.num_players,
                record.stack_bb,
                player_ids_json,
                record.actions,
                showdown_json,
                record.pot_bb,
            ],
        ).map_err(|e| format!("Failed to insert hand: {}", e))?;

        let hand_id = self.conn.last_insert_rowid();

        // Update player stats
        for (pos, player_id) in record.player_ids.iter().enumerate() {
            if player_id.is_empty() {
                continue;
            }
            let action = record.actions.chars().nth(pos).unwrap_or('F');
            let is_allin = action == 'A';
            let showdown_card = record.showdown_cards.get(pos)
                .map(|s| s.as_str())
                .unwrap_or("");

            self.update_player_stats(
                player_id,
                pos as u8,
                record.num_players,
                is_allin,
                showdown_card,
            )?;
        }

        Ok(hand_id)
    }

    /// Update statistics for a single player.
    fn update_player_stats(
        &self,
        player_id: &str,
        _position: u8,
        _num_players: u8,
        is_allin: bool,
        showdown_card: &str,
    ) -> Result<(), String> {
        // Ensure player exists
        self.conn.execute(
            "INSERT OR IGNORE INTO player_stats (player_id, total_hands, allin_count, fold_count, showdown_count, allin_by_position, hands_by_position, showdown_hands)
             VALUES (?1, 0, 0, 0, 0, '[]', '[]', '[]')",
            params![player_id],
        ).map_err(|e| format!("Failed to upsert player: {}", e))?;

        // Increment counters
        if is_allin {
            self.conn.execute(
                "UPDATE player_stats SET total_hands = total_hands + 1, allin_count = allin_count + 1 WHERE player_id = ?1",
                params![player_id],
            ).map_err(|e| format!("Failed to update stats: {}", e))?;
        } else {
            self.conn.execute(
                "UPDATE player_stats SET total_hands = total_hands + 1, fold_count = fold_count + 1 WHERE player_id = ?1",
                params![player_id],
            ).map_err(|e| format!("Failed to update stats: {}", e))?;
        }

        // Record showdown hand if shown
        if !showdown_card.is_empty() {
            self.conn.execute(
                "UPDATE player_stats SET showdown_count = showdown_count + 1 WHERE player_id = ?1",
                params![player_id],
            ).map_err(|e| format!("Failed to update showdown count: {}", e))?;

            // Append to showdown_hands JSON array
            let current: String = self.conn.query_row(
                "SELECT showdown_hands FROM player_stats WHERE player_id = ?1",
                params![player_id],
                |row| row.get(0),
            ).map_err(|e| format!("Failed to query showdown hands: {}", e))?;

            let mut hands: Vec<String> = serde_json::from_str(&current).unwrap_or_default();
            hands.push(showdown_card.to_string());
            let updated = serde_json::to_string(&hands)
                .map_err(|e| format!("JSON error: {}", e))?;

            self.conn.execute(
                "UPDATE player_stats SET showdown_hands = ?1 WHERE player_id = ?2",
                params![updated, player_id],
            ).map_err(|e| format!("Failed to update showdown hands: {}", e))?;
        }

        Ok(())
    }

    /// Get statistics for a player.
    pub fn get_player_stats(&self, player_id: &str) -> Result<Option<PlayerStats>, String> {
        let result = self.conn.query_row(
            "SELECT total_hands, allin_count, fold_count, showdown_count, showdown_hands
             FROM player_stats WHERE player_id = ?1",
            params![player_id],
            |row| {
                Ok(PlayerStats {
                    player_id: player_id.to_string(),
                    total_hands: row.get(0)?,
                    allin_count: row.get(1)?,
                    fold_count: row.get(2)?,
                    showdown_count: row.get(3)?,
                    showdown_hands_json: row.get(4)?,
                })
            },
        );

        match result {
            Ok(stats) => Ok(Some(stats)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(format!("DB error: {}", e)),
        }
    }
}

/// Player statistics summary.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlayerStats {
    pub player_id: String,
    pub total_hands: u32,
    pub allin_count: u32,
    pub fold_count: u32,
    pub showdown_count: u32,
    pub showdown_hands_json: String,
}

impl PlayerStats {
    /// VPIP (Voluntarily Put In Pot) = all-in percentage.
    pub fn vpip(&self) -> f64 {
        if self.total_hands == 0 {
            0.5 // Default assumption
        } else {
            self.allin_count as f64 / self.total_hands as f64
        }
    }

    /// Get showdown hands as canonical hand strings.
    pub fn showdown_hands(&self) -> Vec<String> {
        serde_json::from_str(&self.showdown_hands_json).unwrap_or_default()
    }

    /// Confidence level (0.0 = no data, 1.0 = highly confident).
    /// Based on sample size.
    pub fn confidence(&self) -> f64 {
        // Sigmoid-like: reaches ~0.9 at 100 hands
        let n = self.total_hands as f64;
        1.0 - 1.0 / (1.0 + n / 30.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_hand_history_db() {
        let db = HandHistoryDb::open(":memory:").unwrap();

        let record = HandRecord {
            timestamp: "2024-01-01T00:00:00Z".to_string(),
            num_players: 4,
            stack_bb: 8.0,
            player_ids: vec!["p1".into(), "p2".into(), "p3".into(), "p4".into()],
            actions: "FAFA".to_string(),
            showdown_cards: vec!["".into(), "AcKd".into(), "".into(), "QhQs".into()],
            pot_bb: 17.5,
        };

        let id = db.record_hand(&record).unwrap();
        assert!(id > 0);

        // Check stats for p2 (went all-in, showed AcKd)
        let stats = db.get_player_stats("p2").unwrap().unwrap();
        assert_eq!(stats.total_hands, 1);
        assert_eq!(stats.allin_count, 1);
        assert_eq!(stats.showdown_count, 1);
        assert!((stats.vpip() - 1.0).abs() < 1e-10);

        // Check stats for p1 (folded)
        let stats = db.get_player_stats("p1").unwrap().unwrap();
        assert_eq!(stats.total_hands, 1);
        assert_eq!(stats.fold_count, 1);
        assert!((stats.vpip() - 0.0).abs() < 1e-10);
    }

    #[test]
    fn test_confidence() {
        let stats = PlayerStats {
            player_id: "test".to_string(),
            total_hands: 0,
            allin_count: 0,
            fold_count: 0,
            showdown_count: 0,
            showdown_hands_json: "[]".to_string(),
        };
        assert!(stats.confidence() < 0.1);

        let stats100 = PlayerStats {
            total_hands: 100,
            ..stats.clone()
        };
        assert!(stats100.confidence() > 0.7);
    }
}
