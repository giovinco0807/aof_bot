//! Push/fold chart generation and export.
//!
//! Generates human-readable and machine-readable charts from solved strategies.
//! Charts are organized by: num_players → stack_size → position → prior_actions → hand.

use crate::aof_state::{AofConfig, prior_action_sequences, AofAction};
use crate::card::{preflop_index_to_str, NUM_PREFLOP_HANDS};
use crate::solver::SolveResult;
use serde::{Deserialize, Serialize};
/// A single chart entry for one hand in one situation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChartEntry {
    /// Canonical hand string, e.g. "AKs", "TT", "72o"
    pub hand: String,
    /// Canonical hand index (0-168)
    pub hand_index: u8,
    /// Probability of going all-in [0.0, 1.0]
    pub allin_freq: f64,
}

/// A chart for one specific decision point.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DecisionChart {
    /// Position name (UTG, BTN, SB, BB)
    pub position: String,
    /// Prior action string, e.g. "FF" (both folded before you)
    pub prior_actions: String,
    /// Description of the situation
    pub description: String,
    /// Entries for all 169 hands, sorted by all-in frequency (descending)
    pub entries: Vec<ChartEntry>,
}

/// Complete chart set for a specific game configuration.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChartSet {
    /// Number of players
    pub num_players: u8,
    /// Stack size in BB
    pub stack_bb: f64,
    /// Blind/ante structure description
    pub structure: String,
    /// All decision charts
    pub charts: Vec<DecisionChart>,
}

/// Generate charts from a solve result.
pub fn generate_charts(result: &SolveResult) -> ChartSet {
    let config = &result.config;
    let n = config.num_players;
    let mut charts = Vec::new();

    for position in 0..n {
        let prior_seqs = prior_action_sequences(position);

        for prior_actions in &prior_seqs {
            let prior_mask = actions_to_mask(prior_actions);
            let prior_str = actions_to_str(prior_actions);

            let mut entries = Vec::with_capacity(NUM_PREFLOP_HANDS);

            for hand_idx in 0..NUM_PREFLOP_HANDS as u8 {
                let allin_freq = result.get_allin_prob(position, hand_idx, prior_mask);
                entries.push(ChartEntry {
                    hand: preflop_index_to_str(hand_idx),
                    hand_index: hand_idx,
                    allin_freq,
                });
            }

            // Sort by all-in frequency descending
            entries.sort_by(|a, b| b.allin_freq.partial_cmp(&a.allin_freq).unwrap());

            let description = format_description(config, position, &prior_str);

            charts.push(DecisionChart {
                position: config.position_name(position).to_string(),
                prior_actions: prior_str,
                description,
                entries,
            });
        }
    }

    ChartSet {
        num_players: n,
        stack_bb: config.stack_bb,
        structure: format!(
            "SB={} BB={} Ante={}",
            config.sb_bb, config.bb_bb, config.ante_bb
        ),
        charts,
    }
}

/// Format a human-readable description of a decision point.
fn format_description(config: &AofConfig, position: u8, prior_str: &str) -> String {
    let pos_name = config.position_name(position);

    if prior_str.is_empty() {
        return format!("{} opens (first to act)", pos_name);
    }

    let mut parts = Vec::new();
    for (i, ch) in prior_str.chars().enumerate() {
        let prior_pos = config.position_name(i as u8);
        match ch {
            'F' => parts.push(format!("{} folds", prior_pos)),
            'A' => parts.push(format!("{} all-in", prior_pos)),
            _ => {}
        }
    }

    format!("{}: after {}", pos_name, parts.join(", "))
}

/// Convert action list to bitmask.
fn actions_to_mask(actions: &[AofAction]) -> u8 {
    let mut mask = 0u8;
    for (i, action) in actions.iter().enumerate() {
        if *action == AofAction::AllIn {
            mask |= 1 << i;
        }
    }
    mask
}

/// Convert action list to string.
fn actions_to_str(actions: &[AofAction]) -> String {
    actions.iter().map(|a| a.to_char()).collect()
}

/// Export charts to JSON file.
pub fn export_json(chart_set: &ChartSet, path: &str) -> std::io::Result<()> {
    let json = serde_json::to_string_pretty(chart_set)?;
    std::fs::write(path, json)
}

/// Print a summary of the charts to stdout.
pub fn print_summary(chart_set: &ChartSet) {
    println!(
        "\n=== {}p AoF, {:.1}BB, {} ===\n",
        chart_set.num_players, chart_set.stack_bb, chart_set.structure
    );

    for chart in &chart_set.charts {
        println!("--- {} [{}] ---", chart.position, chart.description);

        // Show hands that push >50%
        let push_hands: Vec<&ChartEntry> = chart
            .entries
            .iter()
            .filter(|e| e.allin_freq > 0.5)
            .collect();

        if push_hands.is_empty() {
            println!("  Never push");
        } else {
            let hand_strs: Vec<String> = push_hands
                .iter()
                .map(|e| {
                    if e.allin_freq > 0.99 {
                        e.hand.clone()
                    } else {
                        format!("{}({:.0}%)", e.hand, e.allin_freq * 100.0)
                    }
                })
                .collect();
            println!("  Push ({}): {}", push_hands.len(), hand_strs.join(", "));
        }
        println!();
    }
}

/// Generate a 13x13 grid chart (like standard poker range charts).
/// Rows = first card rank (A to 2), Columns = second card rank (A to 2).
/// Upper-right triangle = suited, lower-left = offsuit, diagonal = pairs.
pub fn generate_grid_chart(chart: &DecisionChart) -> [[f64; 13]; 13] {
    let mut grid = [[0.0f64; 13]; 13];

    for entry in &chart.entries {
        let idx = entry.hand_index;
        if idx <= 12 {
            // Pocket pair on diagonal
            let r = 12 - idx as usize; // AA at [0][0], 22 at [12][12]
            grid[r][r] = entry.allin_freq;
        } else if idx <= 90 {
            // Suited: upper-right
            let mut n = idx - 13;
            for i in 1u8..13 {
                for j in 0..i {
                    if n == 0 {
                        let row = 12 - i as usize;
                        let col = 12 - j as usize;
                        // Suited goes in upper-right (row < col)
                        grid[row.min(col)][row.max(col)] = entry.allin_freq;
                        break;
                    }
                    n -= 1;
                }
                if n == 0 {
                    break;
                }
            }
        } else {
            // Offsuit: lower-left
            let mut n = idx - 91;
            for i in 1u8..13 {
                for j in 0..i {
                    if n == 0 {
                        let row = 12 - i as usize;
                        let col = 12 - j as usize;
                        // Offsuit goes in lower-left (row > col)
                        grid[row.max(col)][row.min(col)] = entry.allin_freq;
                        break;
                    }
                    n -= 1;
                }
                if n == 0 {
                    break;
                }
            }
        }
    }

    grid
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_actions_to_mask() {
        let actions = vec![AofAction::Fold, AofAction::AllIn, AofAction::Fold];
        assert_eq!(actions_to_mask(&actions), 0b010);
    }

    #[test]
    fn test_actions_to_str() {
        let actions = vec![AofAction::Fold, AofAction::AllIn];
        assert_eq!(actions_to_str(&actions), "FA");
    }
}
