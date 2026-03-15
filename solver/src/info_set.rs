//! Information set data structures and regret matching for AoF CFR.
//!
//! In AoF, the info set key is simply:
//!   (position, hand_index, prior_action_bitmask)
//! Since each player has exactly 2 actions (Fold/AllIn), this is very compact.

use std::collections::HashMap;

/// Data stored per information set.
#[derive(Debug, Clone)]
pub struct InfoSetData {
    /// Cumulative regret for each action [Fold, AllIn]
    pub regret_sum: [f64; 2],
    /// Cumulative weighted strategy for each action [Fold, AllIn]
    pub strategy_sum: [f64; 2],
}

impl InfoSetData {
    pub fn new() -> Self {
        Self {
            regret_sum: [0.0; 2],
            strategy_sum: [0.0; 2],
        }
    }
}

/// Encode an AoF info set key.
/// Layout: position (3 bits) | hand_index (8 bits) | prior_actions bitmask (4 bits)
/// Total: 15 bits, fits in u16 (cast to u64 for HashMap key).
#[inline]
pub fn encode_key(position: u8, hand_index: u8, prior_actions_mask: u8) -> u32 {
    ((position as u32) << 12) | ((hand_index as u32) << 4) | (prior_actions_mask as u32)
}

/// Type alias for the info set table.
pub type InfoSetTable = HashMap<u32, InfoSetData>;

/// Compute current strategy from regret sums via Regret Matching.
#[inline]
pub fn get_strategy(regret_sum: &[f64; 2]) -> [f64; 2] {
    let pos0 = regret_sum[0].max(0.0);
    let pos1 = regret_sum[1].max(0.0);
    let sum = pos0 + pos1;
    if sum > 0.0 {
        [pos0 / sum, pos1 / sum]
    } else {
        [0.5, 0.5]
    }
}

/// Compute the average strategy from cumulative strategy sums.
#[inline]
pub fn get_average_strategy(strategy_sum: &[f64; 2]) -> [f64; 2] {
    let total = strategy_sum[0] + strategy_sum[1];
    if total > 0.0 {
        [strategy_sum[0] / total, strategy_sum[1] / total]
    } else {
        [0.5, 0.5]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_encode_key_unique() {
        let k1 = encode_key(0, 100, 0b0000);
        let k2 = encode_key(0, 100, 0b0001);
        let k3 = encode_key(1, 100, 0b0000);
        assert_ne!(k1, k2);
        assert_ne!(k1, k3);
    }

    #[test]
    fn test_get_strategy_positive() {
        let regrets = [10.0, 30.0];
        let strat = get_strategy(&regrets);
        assert!((strat[0] - 0.25).abs() < 1e-10);
        assert!((strat[1] - 0.75).abs() < 1e-10);
    }

    #[test]
    fn test_get_strategy_all_negative() {
        let regrets = [-5.0, -10.0];
        let strat = get_strategy(&regrets);
        assert!((strat[0] - 0.5).abs() < 1e-10);
        assert!((strat[1] - 0.5).abs() < 1e-10);
    }
}
