//! AoF game state machine for N-player All-in or Fold.
//!
//! Handles 2-4 player configurations. The game tree is:
//!   - Players act in order (position 0 first, then 1, 2, 3)
//!   - Each player: Fold or All-in
//!   - Terminal when all players have acted
//!   - Showdown among all all-in players (+ BB if no one all-ins before BB)

use serde::{Deserialize, Serialize};

/// AoF action: binary choice.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum AofAction {
    Fold,
    AllIn,
}

impl AofAction {
    pub fn to_bit(self) -> u8 {
        match self {
            AofAction::Fold => 0,
            AofAction::AllIn => 1,
        }
    }

    pub fn from_bit(b: u8) -> Self {
        if b == 0 {
            AofAction::Fold
        } else {
            AofAction::AllIn
        }
    }

    pub fn to_char(self) -> char {
        match self {
            AofAction::Fold => 'F',
            AofAction::AllIn => 'A',
        }
    }
}

/// Game configuration for an AoF table.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AofConfig {
    /// Number of players at the table (2-4)
    pub num_players: u8,
    /// Stack size in big blinds
    pub stack_bb: f64,
    /// Small blind size in BB (typically 0.5)
    pub sb_bb: f64,
    /// Big blind size in BB (typically 1.0)
    pub bb_bb: f64,
    /// Ante per player in BB (0.0 for no ante)
    pub ante_bb: f64,
    /// Rake percentage (e.g. 0.02 for 2%)
    pub rake_pct: f64,
    /// Rake cap in BB (e.g. 3.0 for 3BB cap)
    pub rake_cap_bb: f64,
}

impl AofConfig {
    /// Standard 4-player config with given stack size (PPPoker default: 2% rake, 3BB cap).
    pub fn four_player(stack_bb: f64) -> Self {
        Self {
            num_players: 4,
            stack_bb,
            sb_bb: 0.5,
            bb_bb: 1.0,
            ante_bb: 0.0,
            rake_pct: 0.02,
            rake_cap_bb: 3.0,
        }
    }

    /// Standard 3-player config.
    pub fn three_player(stack_bb: f64) -> Self {
        Self {
            num_players: 3,
            stack_bb,
            sb_bb: 0.5,
            bb_bb: 1.0,
            ante_bb: 0.0,
            rake_pct: 0.02,
            rake_cap_bb: 3.0,
        }
    }

    /// Heads-up config.
    pub fn heads_up(stack_bb: f64) -> Self {
        Self {
            num_players: 2,
            stack_bb,
            sb_bb: 0.5,
            bb_bb: 1.0,
            ante_bb: 0.0,
            rake_pct: 0.02,
            rake_cap_bb: 3.0,
        }
    }

    /// Config with no rake (for testing or comparison).
    pub fn no_rake(num_players: u8, stack_bb: f64) -> Self {
        Self {
            num_players,
            stack_bb,
            sb_bb: 0.5,
            bb_bb: 1.0,
            ante_bb: 0.0,
            rake_pct: 0.0,
            rake_cap_bb: 0.0,
        }
    }

    /// Compute rake amount for a given pot size.
    pub fn compute_rake(&self, pot: f64) -> f64 {
        (pot * self.rake_pct).min(self.rake_cap_bb)
    }

    /// Total pot size at the start (blinds + antes).
    pub fn initial_pot(&self) -> f64 {
        self.sb_bb + self.bb_bb + self.ante_bb * self.num_players as f64
    }

    /// Amount a player risks by going all-in (stack minus what's already posted).
    pub fn allin_risk(&self, position: u8) -> f64 {
        let posted = self.posted_amount(position);
        self.stack_bb - posted
    }

    /// Amount already posted by a given position.
    pub fn posted_amount(&self, position: u8) -> f64 {
        let blind = match self.position_role(position) {
            PositionRole::SB => self.sb_bb,
            PositionRole::BB => self.bb_bb,
            _ => 0.0,
        };
        blind + self.ante_bb
    }

    /// Get the role for a position index.
    /// For 4 players: 0=UTG, 1=BTN, 2=SB, 3=BB
    /// For 3 players: 0=BTN, 1=SB, 2=BB
    /// For 2 players (HU): 0=SB/BTN, 1=BB
    pub fn position_role(&self, position: u8) -> PositionRole {
        match self.num_players {
            2 => match position {
                0 => PositionRole::SB, // SB acts first in HU AoF
                1 => PositionRole::BB,
                _ => unreachable!(),
            },
            3 => match position {
                0 => PositionRole::BTN,
                1 => PositionRole::SB,
                2 => PositionRole::BB,
                _ => unreachable!(),
            },
            4 => match position {
                0 => PositionRole::UTG,
                1 => PositionRole::BTN,
                2 => PositionRole::SB,
                3 => PositionRole::BB,
                _ => unreachable!(),
            },
            _ => unreachable!("unsupported player count"),
        }
    }

    /// Get the position name string.
    pub fn position_name(&self, position: u8) -> &'static str {
        self.position_role(position).name()
    }
}

/// Position roles at the table.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PositionRole {
    UTG,
    BTN,
    SB,
    BB,
}

impl PositionRole {
    pub fn name(self) -> &'static str {
        match self {
            PositionRole::UTG => "UTG",
            PositionRole::BTN => "BTN",
            PositionRole::SB => "SB",
            PositionRole::BB => "BB",
        }
    }
}

/// Current state of an AoF hand.
#[derive(Debug, Clone)]
pub struct AofState {
    pub config: AofConfig,
    /// Actions taken so far (one per player who has acted).
    pub actions: Vec<AofAction>,
    /// Is the hand over?
    pub is_terminal: bool,
}

impl AofState {
    pub fn new(config: AofConfig) -> Self {
        Self {
            config,
            actions: Vec::new(),
            is_terminal: false,
        }
    }

    /// Which position acts next (0-based).
    pub fn current_position(&self) -> u8 {
        self.actions.len() as u8
    }

    /// Number of players who have gone all-in so far.
    pub fn allin_count(&self) -> usize {
        self.actions.iter().filter(|a| **a == AofAction::AllIn).count()
    }

    /// Apply an action and return the new state.
    pub fn apply(&self, action: AofAction) -> Self {
        let mut new_state = self.clone();
        new_state.actions.push(action);

        if new_state.actions.len() == new_state.config.num_players as usize {
            new_state.is_terminal = true;
        }

        new_state
    }

    /// Encode the prior action history as a bitmask.
    /// Bit i = 1 means player i went all-in, 0 means fold.
    pub fn action_bitmask(&self) -> u8 {
        let mut mask = 0u8;
        for (i, action) in self.actions.iter().enumerate() {
            if *action == AofAction::AllIn {
                mask |= 1 << i;
            }
        }
        mask
    }

    /// Encode the prior actions as a string like "FA" (pos0=Fold, pos1=AllIn).
    pub fn action_str(&self) -> String {
        self.actions.iter().map(|a| a.to_char()).collect()
    }

    /// Compute the terminal payoff for a given player.
    ///
    /// `hand_indices`: canonical hand index (0-168) for each player.
    /// `equity_fn`: function that computes equity given a set of all-in player indices and their hands.
    ///
    /// Returns payoff in BB (profit/loss from the hand).
    pub fn terminal_payoff<F>(
        &self,
        player: u8,
        hand_indices: &[u8],
        equity_fn: &F,
    ) -> f64
    where
        F: Fn(&[(u8, u8)]) -> Vec<f64>, // (position, hand_index) pairs -> equity for each
    {
        assert!(self.is_terminal);

        let n = self.config.num_players as usize;
        let allin_players: Vec<u8> = (0..n as u8)
            .filter(|&i| self.actions[i as usize] == AofAction::AllIn)
            .collect();

        let player_action = self.actions[player as usize];

        if allin_players.is_empty() {
            // Everyone folded: BB wins the pot (SB + antes)
            let bb_pos = self.config.num_players - 1; // BB is last position
            if player == bb_pos {
                // BB wins: SB + all antes (minus BB's own blind which is already posted)
                return self.config.initial_pot() - self.config.posted_amount(player);
            } else {
                // Lost what was posted
                return -self.config.posted_amount(player);
            }
        }

        if allin_players.len() == 1 {
            let winner = allin_players[0];
            if player == winner {
                // Won pot uncontested: collect all posted amounts
                return self.config.initial_pot() - self.config.posted_amount(player);
            } else {
                return -self.config.posted_amount(player);
            }
        }

        // Multiple all-ins: showdown
        if player_action == AofAction::Fold {
            // Folded, lost posted amount
            return -self.config.posted_amount(player);
        }

        // Player is all-in, compute equity share of the pot
        let matchup: Vec<(u8, u8)> = allin_players
            .iter()
            .map(|&p| (p, hand_indices[p as usize]))
            .collect();
        let equities = equity_fn(&matchup);

        // Find this player's index in the all-in list
        let player_idx = allin_players.iter().position(|&p| p == player).unwrap();
        let equity = equities[player_idx];

        // Total pot = all all-in stacks + folded players' posted amounts
        let total_pot: f64 = (0..n as u8)
            .map(|p| {
                if self.actions[p as usize] == AofAction::AllIn {
                    self.config.stack_bb
                } else {
                    self.config.posted_amount(p)
                }
            })
            .sum();

        // Payoff = equity share of pot - amount invested
        equity * total_pot - self.config.stack_bb
    }
}

/// All possible prior action histories for a given number of players.
/// Returns bitmasks representing each unique sequence.
pub fn all_action_histories(num_players: u8) -> Vec<Vec<AofAction>> {
    if num_players == 0 {
        return vec![vec![]];
    }
    let sub = all_action_histories(num_players - 1);
    let mut result = Vec::new();
    for history in &sub {
        let mut h1 = history.clone();
        h1.push(AofAction::Fold);
        result.push(h1);
        let mut h2 = history.clone();
        h2.push(AofAction::AllIn);
        result.push(h2);
    }
    result
}

/// Returns all possible prior action sequences that a player at `position`
/// would see before making their decision.
pub fn prior_action_sequences(position: u8) -> Vec<Vec<AofAction>> {
    all_action_histories(position)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_4player_positions() {
        let config = AofConfig::four_player(5.0);
        assert_eq!(config.position_role(0), PositionRole::UTG);
        assert_eq!(config.position_role(1), PositionRole::BTN);
        assert_eq!(config.position_role(2), PositionRole::SB);
        assert_eq!(config.position_role(3), PositionRole::BB);
    }

    #[test]
    fn test_action_histories() {
        let histories = prior_action_sequences(2);
        // 2 prior players => 2^2 = 4 histories
        assert_eq!(histories.len(), 4);
    }

    #[test]
    fn test_action_bitmask() {
        let config = AofConfig::four_player(5.0);
        let state = AofState::new(config)
            .apply(AofAction::Fold)
            .apply(AofAction::AllIn)
            .apply(AofAction::Fold);
        assert_eq!(state.action_bitmask(), 0b010); // only pos 1 all-in
        assert_eq!(state.action_str(), "FAF");
    }

    #[test]
    fn test_terminal_detection() {
        let config = AofConfig::four_player(5.0);
        let s = AofState::new(config);
        assert!(!s.is_terminal);
        let s = s.apply(AofAction::Fold);
        assert!(!s.is_terminal);
        let s = s.apply(AofAction::Fold);
        assert!(!s.is_terminal);
        let s = s.apply(AofAction::Fold);
        assert!(!s.is_terminal);
        let s = s.apply(AofAction::Fold);
        assert!(s.is_terminal);
    }

    #[test]
    fn test_initial_pot() {
        let config = AofConfig::four_player(5.0);
        // SB=0.5, BB=1.0, ante=0 => pot=1.5
        assert!((config.initial_pot() - 1.5).abs() < 1e-10);
    }

    #[test]
    fn test_initial_pot_with_ante() {
        let mut config = AofConfig::four_player(5.0);
        config.ante_bb = 0.25;
        // SB=0.5, BB=1.0, ante=0.25*4=1.0 => pot=2.5
        assert!((config.initial_pot() - 2.5).abs() < 1e-10);
    }
}
