//! EHS (Expected Hand Strength) computation.
//!
//! - `compute_ehs_exact`: Full enumeration over all C(45,2)=990 opponent hands (river).
//! - `compute_ehs_monte_carlo`: Sampling-based EHS (flop/turn).

use crate::card::{evaluate_7cards, NUM_CARDS};
use rayon::prelude::*;

/// Exact EHS for river (5 board cards known).
///
/// Enumerates all C(remaining, 2) opponent holdings and computes
/// win rate + 0.5 * tie rate.
pub fn compute_ehs_exact(hole: (u8, u8), board: &[u8]) -> f64 {
    debug_assert!(board.len() == 5, "exact EHS requires 5 board cards");

    let used: u64 =
        card_mask(hole.0) | card_mask(hole.1) | board.iter().fold(0u64, |m, &c| m | card_mask(c));

    let remaining: Vec<u8> = (0..NUM_CARDS as u8)
        .filter(|&c| used & card_mask(c) == 0)
        .collect();

    let my_cards: Vec<u8> = vec![
        hole.0, hole.1, board[0], board[1], board[2], board[3], board[4],
    ];
    let my_rank = evaluate_7cards(&my_cards);

    let mut wins = 0.0f64;
    let mut total = 0u32;

    for i in 0..remaining.len() {
        for j in (i + 1)..remaining.len() {
            let opp_cards: Vec<u8> = vec![
                remaining[i],
                remaining[j],
                board[0],
                board[1],
                board[2],
                board[3],
                board[4],
            ];
            let opp_rank = evaluate_7cards(&opp_cards);
            if my_rank > opp_rank {
                wins += 1.0;
            } else if my_rank == opp_rank {
                wins += 0.5;
            }
            total += 1;
        }
    }

    if total == 0 {
        0.5
    } else {
        wins / total as f64
    }
}

/// Monte Carlo EHS (for flop/turn where enumeration is too expensive).
pub fn compute_ehs_monte_carlo(
    hole: (u8, u8),
    board: &[u8],
    num_samples: u32,
    rng: &mut impl rand::Rng,
) -> f64 {
    let used: u64 =
        card_mask(hole.0) | card_mask(hole.1) | board.iter().fold(0u64, |m, &c| m | card_mask(c));

    let remaining: Vec<u8> = (0..NUM_CARDS as u8)
        .filter(|&c| used & card_mask(c) == 0)
        .collect();
    let cards_needed = 2 + (5 - board.len());

    let mut wins = 0.0f64;

    for _ in 0..num_samples {
        let mut buf = remaining.clone();
        for k in 0..cards_needed {
            let idx = rng.gen_range(k..buf.len());
            buf.swap(k, idx);
        }

        let opp = (buf[0], buf[1]);
        let mut full_board: Vec<u8> = board.to_vec();
        for k in 2..cards_needed {
            full_board.push(buf[k]);
        }

        let mut my_cards = vec![hole.0, hole.1];
        my_cards.extend_from_slice(&full_board);
        let mut opp_cards = vec![opp.0, opp.1];
        opp_cards.extend_from_slice(&full_board);

        let my_rank = evaluate_7cards(&my_cards);
        let opp_rank = evaluate_7cards(&opp_cards);

        if my_rank > opp_rank {
            wins += 1.0;
        } else if my_rank == opp_rank {
            wins += 0.5;
        }
    }

    wins / num_samples as f64
}

/// Parallel batch EHS computation for multiple (hole, board) pairs.
pub fn compute_ehs_batch_exact(tasks: &[((u8, u8), Vec<u8>)]) -> Vec<f64> {
    tasks
        .par_iter()
        .map(|(hole, board)| compute_ehs_exact(*hole, board))
        .collect()
}

#[inline]
fn card_mask(card: u8) -> u64 {
    1u64 << card
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::card::str_to_card;

    fn make_cards(strs: &[&str]) -> Vec<u8> {
        strs.iter().map(|s| str_to_card(s)).collect()
    }

    #[test]
    fn test_ehs_exact_nut_hand() {
        let hole = (str_to_card("Ac"), str_to_card("Kc"));
        let board = make_cards(&["Qc", "Jc", "Tc", "2d", "3h"]);
        let ehs = compute_ehs_exact(hole, &board);
        assert!(ehs > 0.99, "royal flush EHS = {}, expected > 0.99", ehs);
    }

    #[test]
    fn test_ehs_exact_weak_hand() {
        let hole = (str_to_card("7c"), str_to_card("2d"));
        let board = make_cards(&["Ac", "Kd", "Qh", "Js", "9c"]);
        let ehs = compute_ehs_exact(hole, &board);
        assert!(ehs < 0.15, "72o on AKQJ9 EHS = {}, expected < 0.15", ehs);
    }
}
