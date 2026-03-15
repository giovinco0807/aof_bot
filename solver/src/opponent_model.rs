//! Bayesian opponent modeling for exploitative play.
//!
//! Based on:
//! - Southey et al. (2005): "Bayes' Bluff: Opponent Modelling in Poker" (UAI 2005)
//!   Uses Dirichlet/Beta priors for opponent action probabilities.
//! - Ganzfried & Sandholm (2015): "Safe Opponent Exploitation" (TEAC)
//!   Constrains deviation from Nash proportional to model confidence.
//!
//! For AoF, each opponent's strategy is a 169-element vector of push probabilities.
//! We model each hand's push probability with a Beta(α, β) distribution:
//!   - Prior: Beta(1, 1) = Uniform (maximum ignorance)
//!   - Posterior: Beta(1 + pushes, 1 + folds) after observing actions
//!   - Mean: α / (α + β) = (1 + pushes) / (2 + pushes + folds)
//!
//! Position-specific statistics further refine the model.

use crate::hand_history::PlayerStats;

/// Per-hand Bayesian push estimate using Beta distribution.
#[derive(Debug, Clone)]
pub struct BetaEstimate {
    /// Alpha parameter (1 + observed pushes with this hand)
    pub alpha: f64,
    /// Beta parameter (1 + observed folds with this hand)
    pub beta: f64,
}

impl BetaEstimate {
    /// Create uninformative prior Beta(1, 1).
    pub fn uniform_prior() -> Self {
        Self {
            alpha: 1.0,
            beta: 1.0,
        }
    }

    /// Posterior mean = α / (α + β)
    pub fn mean(&self) -> f64 {
        self.alpha / (self.alpha + self.beta)
    }

    /// Posterior variance = αβ / ((α+β)²(α+β+1))
    /// Lower variance = more confident
    pub fn variance(&self) -> f64 {
        let ab = self.alpha + self.beta;
        (self.alpha * self.beta) / (ab * ab * (ab + 1.0))
    }

    /// Number of observations (pseudo-count minus prior)
    pub fn observations(&self) -> f64 {
        (self.alpha - 1.0) + (self.beta - 1.0)
    }
}

/// Estimated opponent range (probability of pushing each canonical hand).
#[derive(Debug, Clone)]
pub struct OpponentRange {
    /// Push probability for each of 169 canonical hands.
    pub push_freq: [f64; 169],
}

impl OpponentRange {
    /// Create a uniform range (50/50 for each hand).
    pub fn uniform() -> Self {
        Self {
            push_freq: [0.5; 169],
        }
    }

    /// Estimate range from player statistics using Bayesian inference.
    ///
    /// Two-level model:
    /// 1. Overall VPIP → Beta prior on per-hand push rates
    ///    (informed by hand strength ranking)
    /// 2. Showdown hands → direct Beta updates for observed hands
    ///
    /// Based on Southey et al. (2005) "Bayes' Bluff" approach.
    pub fn from_stats(stats: &PlayerStats) -> Self {
        let vpip = stats.vpip();
        let confidence = stats.confidence();
        let total_hands = stats.total_hands as f64;

        // If very few observations, return near-uniform
        if total_hands < 5.0 || confidence < 0.05 {
            return Self::uniform();
        }

        let mut range = Self::uniform();

        // Level 1: VPIP-based prior using hand strength ordering
        let hand_rankings = get_hand_strength_ranking();

        for (rank_pos, &hand_idx) in hand_rankings.iter().enumerate() {
            let rank_percentile = rank_pos as f64 / 169.0;

            // Smooth transition around VPIP boundary using Beta priors
            let prior = if rank_percentile < vpip * 0.8 {
                BetaEstimate { alpha: 2.0, beta: 1.0 }
            } else if rank_percentile < vpip * 1.2 {
                let t = (rank_percentile - vpip * 0.8) / (vpip * 0.4).max(0.01);
                BetaEstimate { alpha: 2.0 - t, beta: 1.0 + t }
            } else {
                BetaEstimate { alpha: 1.0, beta: 2.0 }
            };

            let vpip_weight = confidence;
            range.push_freq[hand_idx as usize] =
                vpip_weight * prior.mean() + (1.0 - vpip_weight) * 0.5;
        }

        // Level 2: Showdown data refinement
        let showdown_hands = stats.showdown_hands();
        if showdown_hands.len() >= 3 {
            refine_range_from_showdowns(&mut range, &showdown_hands, confidence);
        }

        range
    }

    /// Get the estimated VPIP (push frequency weighted by combos).
    pub fn estimated_vpip(&self) -> f64 {
        let mut total_combos = 0u32;
        let mut push_combos = 0.0f64;

        for idx in 0..169u8 {
            let combos = crate::card::preflop_combos(idx);
            total_combos += combos;
            push_combos += self.push_freq[idx as usize] * combos as f64;
        }

        push_combos / total_combos as f64
    }
}

/// Returns hand indices sorted by typical preflop strength (strongest first).
/// Based on standard poker hand rankings for push/fold.
fn get_hand_strength_ranking() -> Vec<u8> {
    use crate::card::{get_preflop_index, str_to_card};

    // Standard hand ranking order (simplified)
    let ranking_order = [
        "AA", "KK", "QQ", "AKs", "JJ", "AKo", "AQs", "TT", "AQo", "AJs",
        "99", "ATs", "AJo", "KQs", "88", "ATo", "KJs", "KQo", "77", "A9s",
        "KTs", "A8s", "KJo", "66", "A9o", "QJs", "A7s", "KTo", "A5s", "A6s",
        "55", "A8o", "QTs", "QJo", "A4s", "K9s", "A3s", "A7o", "A2s", "44",
        "QTo", "K8s", "A6o", "K9o", "JTs", "A5o", "Q9s", "33", "K7s", "A4o",
        "JTo", "K6s", "Q8s", "A3o", "K8o", "J9s", "A2o", "22", "K5s", "Q9o",
        "K4s", "T9s", "K7o", "Q7s", "K3s", "J8s", "K6o", "Q6s", "K2s", "T8s",
        "J9o", "Q5s", "98s", "K5o", "Q8o", "T9o", "J7s", "Q4s", "K4o", "Q3s",
        "87s", "J8o", "K3o", "Q2s", "97s", "T7s", "Q7o", "J6s", "K2o", "76s",
        "Q6o", "T8o", "86s", "J5s", "98o", "Q5o", "96s", "J4s", "65s", "J7o",
        "Q4o", "T6s", "87o", "75s", "J3s", "Q3o", "97o", "J2s", "85s", "T5s",
        "54s", "Q2o", "T7o", "76o", "64s", "J6o", "86o", "T4s", "95s", "74s",
        "96o", "T3s", "J5o", "53s", "65o", "T2s", "84s", "J4o", "75o", "T6o",
        "63s", "94s", "43s", "85o", "J3o", "54o", "93s", "73s", "52s", "T5o",
        "J2o", "64o", "92s", "83s", "42s", "74o", "T4o", "82s", "62s", "95o",
        "53o", "T3o", "72s", "84o", "63o", "32s", "43o", "T2o", "93o", "94o",
        "73o", "52o", "92o", "83o", "42o", "82o", "62o", "72o", "32o",
    ];

    let mut result = Vec::with_capacity(169);
    let mut seen = [false; 169];

    for hand_str in &ranking_order {
        let bytes = hand_str.as_bytes();
        let r1_char = bytes[0] as char;
        let r2_char = bytes[1] as char;
        let is_suited = hand_str.ends_with('s');
        let is_pair = r1_char == r2_char;

        // Create representative cards
        let r1_str = format!("{}c", r1_char);
        let r2_str = if is_pair {
            format!("{}d", r2_char)
        } else if is_suited {
            format!("{}c", r2_char)
        } else {
            format!("{}d", r2_char)
        };

        let c1 = str_to_card(&r1_str);
        let c2 = str_to_card(&r2_str);
        let idx = get_preflop_index(c1, c2);

        if !seen[idx as usize] {
            result.push(idx);
            seen[idx as usize] = true;
        }
    }

    // Add any missing hands
    for i in 0..169u8 {
        if !seen[i as usize] {
            result.push(i);
        }
    }

    result
}

/// Refine range using observed showdown hands (Bayesian update).
///
/// Each showdown observation is a direct data point: this hand was pushed.
/// We use Beta posterior updates per canonical hand.
fn refine_range_from_showdowns(
    range: &mut OpponentRange,
    showdown_hands: &[String],
    confidence: f64,
) {
    use crate::card::{get_preflop_index, str_to_card};

    let mut hand_counts = [0u32; 169];
    let mut total = 0u32;

    for hand_str in showdown_hands {
        if hand_str.len() >= 4 {
            let c1 = str_to_card(&hand_str[0..2]);
            let c2 = str_to_card(&hand_str[2..4]);
            let idx = get_preflop_index(c1, c2);
            hand_counts[idx as usize] += 1;
            total += 1;
        }
    }

    if total == 0 {
        return;
    }

    // Bayesian update: hands seen at showdown are confirmed pushes
    let showdown_weight = (confidence * 0.5).min(0.5);

    for idx in 0..169 {
        if hand_counts[idx] > 0 {
            let count = hand_counts[idx] as f64;
            let evidence_strength = 1.0 - (-0.3 * count).exp(); // Saturates ~0.95
            range.push_freq[idx] = range.push_freq[idx] * (1.0 - showdown_weight * evidence_strength)
                + showdown_weight * evidence_strength;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_hand_strength_ranking() {
        let ranking = get_hand_strength_ranking();
        assert_eq!(ranking.len(), 169);
        // First should be AA (index 12)
        assert_eq!(ranking[0], 12);
    }

    #[test]
    fn test_uniform_range() {
        let range = OpponentRange::uniform();
        assert!((range.estimated_vpip() - 0.5).abs() < 0.01);
    }

    #[test]
    fn test_range_from_tight_player() {
        let stats = PlayerStats {
            player_id: "tight".to_string(),
            total_hands: 200,
            allin_count: 30,
            fold_count: 170,
            showdown_count: 10,
            showdown_hands_json: "[]".to_string(),
        };
        let range = OpponentRange::from_stats(&stats);
        // Tight player: estimated VPIP should be below 50% (pulled toward
        // uniform prior, so won't match raw 15% exactly)
        let estimated = range.estimated_vpip();
        assert!(
            estimated < 0.5,
            "Tight player estimated VPIP should be <50%, got {:.1}%",
            estimated * 100.0
        );
    }

    #[test]
    fn test_beta_estimate() {
        let prior = BetaEstimate::uniform_prior();
        assert!((prior.mean() - 0.5).abs() < 1e-10);

        // After 7 pushes, 3 folds: Beta(8, 4), mean = 8/12 ≈ 0.667
        let est = BetaEstimate { alpha: 8.0, beta: 4.0 };
        assert!((est.mean() - 8.0 / 12.0).abs() < 1e-10);
        assert!((est.observations() - 10.0).abs() < 1e-10);
    }

    #[test]
    fn test_range_from_loose_player() {
        let stats = PlayerStats {
            player_id: "loose".to_string(),
            total_hands: 200,
            allin_count: 140,
            fold_count: 60,
            showdown_count: 20,
            showdown_hands_json: "[]".to_string(),
        };
        let range = OpponentRange::from_stats(&stats);
        let estimated = range.estimated_vpip();
        assert!(
            estimated > 0.5,
            "Loose player estimated VPIP should be >50%, got {:.1}%",
            estimated * 100.0
        );
    }
}
