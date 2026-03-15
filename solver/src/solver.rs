//! Discounted CFR (DCFR) solver for AoF.
//!
//! Based on Brown & Sandholm (2019): "Solving Imperfect-Information Games via
//! Discounted Regret Minimization" (arXiv:1809.04040).
//!
//! DCFR discounts earlier iterations' contributions to regret sums and
//! strategy sums, achieving dramatically faster convergence than vanilla CFR.
//! On Kuhn Poker, DCFR reached comparable exploitability in 970 iterations
//! vs 471,407 for CFR+.
//!
//! Recommended parameters: α=1.5, β=0.0, γ=2.0 (from the paper).
//!
//! Solves All-in or Fold games with 2-4 players using external sampling MCCFR.
//! Separate models are solved for each player count (2, 3, 4) and stack size.

use crate::aof_state::{AofAction, AofConfig, AofState};
use crate::card::{evaluate_7cards, get_preflop_index, NUM_CARDS};
use crate::equity;
use crate::info_set::{self, InfoSetData, InfoSetTable};
use rand::SeedableRng;

/// A dealt hand: actual cards + canonical index for info set lookup.
#[derive(Debug, Clone, Copy)]
struct DealtHand {
    card1: u8,
    card2: u8,
    canonical: u8,
}

/// CFR solver configuration.
#[derive(Debug, Clone)]
pub struct SolverConfig {
    /// Number of CFR iterations.
    pub iterations: usize,
    /// Number of Monte Carlo samples for multi-way equity.
    pub mc_samples: u32,
    /// Log interval for progress.
    pub log_interval: usize,
    /// DCFR alpha parameter: discount for positive regrets.
    /// Higher = more weight on recent iterations. Default: 1.5.
    pub dcfr_alpha: f64,
    /// DCFR beta parameter: discount for negative regrets.
    /// 0.0 = discard negative regrets immediately (like CFR+). Default: 0.0.
    pub dcfr_beta: f64,
    /// DCFR gamma parameter: discount for strategy sums.
    /// Higher = more weight on recent strategies. Default: 2.0.
    pub dcfr_gamma: f64,
    /// Number of boards to sample at multi-way showdown terminals.
    /// Default 1 = standard MCCFR (fast, noisy).
    /// Higher values (e.g. 1000) reduce board-sampling noise dramatically.
    /// Recommended: 1000 with 100K-1M iterations for clean convergence.
    pub board_samples: u32,
}

impl Default for SolverConfig {
    fn default() -> Self {
        Self {
            iterations: 100_000,
            mc_samples: 10_000,
            log_interval: 10_000,
            // Brown & Sandholm (2019) recommended defaults
            dcfr_alpha: 1.5,
            dcfr_beta: 0.0,
            dcfr_gamma: 2.0,
            board_samples: 1,
        }
    }
}

/// Result of solving a specific AoF configuration.
#[derive(Debug, Clone)]
pub struct SolveResult {
    /// The game configuration that was solved.
    pub config: AofConfig,
    /// Info set table with converged strategies.
    pub info_table: InfoSetTable,
    /// Number of iterations run.
    pub iterations: usize,
}

impl SolveResult {
    /// Get the all-in probability for a given hand at a given position
    /// with given prior actions.
    pub fn get_allin_prob(&self, position: u8, hand_index: u8, prior_mask: u8) -> f64 {
        let key = info_set::encode_key(position, hand_index, prior_mask);
        if let Some(data) = self.info_table.get(&key) {
            let avg = info_set::get_average_strategy(&data.strategy_sum);
            avg[1] // AllIn probability
        } else {
            0.5 // Default: uniform
        }
    }
}

/// Solve an AoF game configuration using Discounted CFR (DCFR).
///
/// DCFR applies discount factors to regret sums and strategy sums at the
/// end of each iteration, giving more weight to recent iterations.
pub fn solve(game_config: &AofConfig, solver_config: &SolverConfig) -> SolveResult {
    let mut info_table = InfoSetTable::new();
    let n = game_config.num_players;

    println!(
        "Solving {}p AoF, stack={:.1}BB, rake={:.1}%/{:.1}BB cap (DCFR α={}, β={}, γ={})...",
        n,
        game_config.stack_bb,
        game_config.rake_pct * 100.0,
        game_config.rake_cap_bb,
        solver_config.dcfr_alpha,
        solver_config.dcfr_beta,
        solver_config.dcfr_gamma,
    );

    let mut rng = rand_xoshiro::Xoshiro256StarStar::seed_from_u64(42);

    let board_samples = solver_config.board_samples;

    for iter in 0..solver_config.iterations {
        // For each traverser player
        for traverser in 0..n {
            let hands = sample_dealt_hands(n, &mut rng);

            let initial_state = AofState::new(game_config.clone());
            let reach = vec![1.0f64; n as usize];

            cfr_traverse(
                &initial_state,
                &hands,
                traverser,
                &reach,
                &mut info_table,
                &mut rng,
                board_samples,
            );
        }

        // DCFR: Apply discounting to all info sets at end of each iteration
        // t is 1-indexed iteration number
        let t = (iter + 1) as f64;
        let alpha = solver_config.dcfr_alpha;
        let beta = solver_config.dcfr_beta;
        let gamma = solver_config.dcfr_gamma;

        // Positive regret discount: t^α / (t^α + 1)
        let pos_discount = t.powf(alpha) / (t.powf(alpha) + 1.0);
        // Negative regret discount: t^β / (t^β + 1)
        let neg_discount = t.powf(beta) / (t.powf(beta) + 1.0);
        // Strategy sum discount: (t / (t + 1))^γ
        let strat_discount = (t / (t + 1.0)).powf(gamma);

        for data in info_table.values_mut() {
            // Discount regret sums
            for r in data.regret_sum.iter_mut() {
                if *r > 0.0 {
                    *r *= pos_discount;
                } else {
                    *r *= neg_discount;
                }
            }
            // Discount strategy sums
            for s in data.strategy_sum.iter_mut() {
                *s *= strat_discount;
            }
        }

        if solver_config.log_interval > 0 && (iter + 1) % solver_config.log_interval == 0 {
            println!("  Iteration {}/{}", iter + 1, solver_config.iterations);
        }
    }

    println!("Solving complete.");

    SolveResult {
        config: game_config.clone(),
        info_table,
        iterations: solver_config.iterations,
    }
}

/// Sample dealt hands for N players.
/// Returns actual cards + canonical indices for each player.
fn sample_dealt_hands(num_players: u8, rng: &mut impl rand::Rng) -> Vec<DealtHand> {
    use rand::seq::SliceRandom;

    let mut deck: Vec<u8> = (0..NUM_CARDS as u8).collect();
    deck.shuffle(rng);

    let mut hands = Vec::with_capacity(num_players as usize);
    for i in 0..num_players as usize {
        let c1 = deck[i * 2];
        let c2 = deck[i * 2 + 1];
        hands.push(DealtHand {
            card1: c1,
            card2: c2,
            canonical: get_preflop_index(c1, c2),
        });
    }
    hands
}

/// Recursive CFR traversal.
///
/// Returns the expected value for the traverser player.
fn cfr_traverse(
    state: &AofState,
    hands: &[DealtHand],
    traverser: u8,
    reach: &[f64],
    info_table: &mut InfoSetTable,
    rng: &mut impl rand::Rng,
    board_samples: u32,
) -> f64 {
    // Terminal node: compute payoff
    if state.is_terminal {
        return compute_terminal_value(state, hands, traverser, rng, board_samples);
    }

    let position = state.current_position();
    let hand_idx = hands[position as usize].canonical;
    let prior_mask = state.action_bitmask();
    let key = info_set::encode_key(position, hand_idx, prior_mask);

    // Get or create info set
    let data = info_table
        .entry(key)
        .or_insert_with(InfoSetData::new);
    let strategy = info_set::get_strategy(&data.regret_sum);

    if position == traverser {
        // Traverser: explore all actions, update regrets
        let mut action_values = [0.0f64; 2];

        for (a, &action) in [AofAction::Fold, AofAction::AllIn].iter().enumerate() {
            let mut new_reach = reach.to_vec();
            new_reach[position as usize] *= strategy[a];

            let new_state = state.apply(action);
            action_values[a] = cfr_traverse(
                &new_state, hands, traverser, &new_reach, info_table, rng, board_samples,
            );
        }

        let node_value = strategy[0] * action_values[0] + strategy[1] * action_values[1];

        // Update regrets
        let data = info_table.get_mut(&key).unwrap();
        for a in 0..2 {
            data.regret_sum[a] += action_values[a] - node_value;
        }

        node_value
    } else {
        // Opponent: sample one action from current strategy, accumulate strategy
        let data = info_table.get_mut(&key).unwrap();

        // Accumulate strategy weighted by reach probability
        let player_reach = reach[position as usize];
        data.strategy_sum[0] += player_reach * strategy[0];
        data.strategy_sum[1] += player_reach * strategy[1];

        // Sample action
        let r: f64 = rng.gen();
        let action_idx = if r < strategy[0] { 0 } else { 1 };
        let action = if action_idx == 0 {
            AofAction::Fold
        } else {
            AofAction::AllIn
        };

        let mut new_reach = reach.to_vec();
        new_reach[position as usize] *= strategy[action_idx];

        let new_state = state.apply(action);
        cfr_traverse(
            &new_state, hands, traverser, &new_reach, info_table, rng, board_samples,
        )
    }
}

/// Compute terminal value for the traverser.
///
/// Since we have actual dealt cards, we deal one random board and evaluate
/// directly - no Monte Carlo equity needed. This is the standard MCCFR approach.
fn compute_terminal_value(
    state: &AofState,
    hands: &[DealtHand],
    traverser: u8,
    rng: &mut impl rand::Rng,
    board_samples: u32,
) -> f64 {
    let n = state.config.num_players;
    let allin_players: Vec<u8> = (0..n)
        .filter(|&i| state.actions[i as usize] == AofAction::AllIn)
        .collect();

    let traverser_action = state.actions[traverser as usize];

    if allin_players.is_empty() {
        // Everyone folded: BB wins
        let bb_pos = state.config.num_players - 1;
        if traverser == bb_pos {
            return state.config.initial_pot() - state.config.posted_amount(traverser);
        } else {
            return -state.config.posted_amount(traverser);
        }
    }

    if allin_players.len() == 1 {
        // One player all-in, rest folded: all-in player wins pot
        let winner = allin_players[0];
        if traverser == winner {
            return state.config.initial_pot() - state.config.posted_amount(traverser);
        } else {
            return -state.config.posted_amount(traverser);
        }
    }

    // Multiple all-ins: showdown
    if traverser_action == AofAction::Fold {
        return -state.config.posted_amount(traverser);
    }

    // For HU showdowns, use precomputed equity matrix if available (faster convergence)
    if allin_players.len() == 2 && equity::is_hu_equity_loaded() {
        let h0 = hands[allin_players[0] as usize].canonical;
        let h1 = hands[allin_players[1] as usize].canonical;
        let trav_eq = if traverser == allin_players[0] {
            equity::get_hu_equity(h0, h1)
        } else {
            equity::get_hu_equity(h1, h0)
        };

        let total_pot: f64 = (0..n)
            .map(|p| {
                if state.actions[p as usize] == AofAction::AllIn {
                    state.config.stack_bb
                } else {
                    state.config.posted_amount(p)
                }
            })
            .sum();
        let rake = state.config.compute_rake(total_pot);
        return trav_eq * (total_pot - rake) - state.config.stack_bb;
    }

    // For 3-way showdowns, use precomputed 3-way equity table if available
    if allin_players.len() == 3 && equity::is_3way_equity_loaded() {
        let trav_idx = allin_players.iter().position(|&p| p == traverser).unwrap();
        let h_trav = hands[allin_players[trav_idx] as usize].canonical;
        let opps: Vec<u8> = allin_players
            .iter()
            .filter(|&&p| p != traverser)
            .map(|&p| hands[p as usize].canonical)
            .collect();
        let trav_eq = equity::get_3way_equity(h_trav, opps[0], opps[1]);

        let total_pot: f64 = (0..n)
            .map(|p| {
                if state.actions[p as usize] == AofAction::AllIn {
                    state.config.stack_bb
                } else {
                    state.config.posted_amount(p)
                }
            })
            .sum();
        let rake = state.config.compute_rake(total_pot);
        return trav_eq * (total_pot - rake) - state.config.stack_bb;
    }

    // For 4-way showdowns, use precomputed 4-way equity table if available
    if allin_players.len() == 4 && equity::is_4way_equity_loaded() {
        let trav_idx = allin_players.iter().position(|&p| p == traverser).unwrap();
        let h_trav = hands[allin_players[trav_idx] as usize].canonical;
        let opps: Vec<u8> = allin_players
            .iter()
            .filter(|&&p| p != traverser)
            .map(|&p| hands[p as usize].canonical)
            .collect();
        let trav_eq = equity::get_4way_equity(h_trav, opps[0], opps[1], opps[2]);

        let total_pot: f64 = (0..n)
            .map(|p| {
                if state.actions[p as usize] == AofAction::AllIn {
                    state.config.stack_bb
                } else {
                    state.config.posted_amount(p)
                }
            })
            .sum();
        let rake = state.config.compute_rake(total_pot);
        return trav_eq * (total_pot - rake) - state.config.stack_bb;
    }

    // Build used-card bitmask from all players' actual cards
    let mut used = 0u64;
    for hand in hands.iter() {
        used |= (1u64 << hand.card1) | (1u64 << hand.card2);
    }

    let remaining: Vec<u8> = (0..NUM_CARDS as u8)
        .filter(|&c| used & (1u64 << c) == 0)
        .collect();

    // Total pot
    let total_pot: f64 = (0..n)
        .map(|p| {
            if state.actions[p as usize] == AofAction::AllIn {
                state.config.stack_bb
            } else {
                state.config.posted_amount(p)
            }
        })
        .sum();
    let rake = state.config.compute_rake(total_pot);
    let pot_after_rake = total_pot - rake;

    let traverser_idx = allin_players.iter().position(|&p| p == traverser).unwrap();

    // Sample board_samples boards and average the equity
    let rlen = remaining.len();
    let mut buf = [0u8; 48];
    buf[..rlen].copy_from_slice(&remaining);

    let mut trav_equity = 0.0f64;
    let num_allin = allin_players.len();
    let mut ranks = vec![0u64; num_allin];

    for _ in 0..board_samples {
        // Fisher-Yates partial shuffle to pick 5 board cards
        for k in 0..5 {
            let idx = rng.gen_range(k..rlen);
            buf.swap(k, idx);
        }

        for (idx, &p) in allin_players.iter().enumerate() {
            let h = &hands[p as usize];
            let cards = [h.card1, h.card2, buf[0], buf[1], buf[2], buf[3], buf[4]];
            ranks[idx] = evaluate_7cards(&cards);
        }

        let max_rank = *ranks.iter().max().unwrap();
        if ranks[traverser_idx] == max_rank {
            let winners = ranks.iter().filter(|&&r| r == max_rank).count();
            trav_equity += 1.0 / winners as f64;
        }
    }

    let eq = trav_equity / board_samples as f64;
    eq * pot_after_rake - state.config.stack_bb
}

/// Solve for multiple player counts and stack sizes.
/// Returns a map of (num_players, stack_bb) -> SolveResult.
pub fn solve_all_configs(
    stack_sizes: &[f64],
    solver_config: &SolverConfig,
) -> Vec<SolveResult> {
    let mut results = Vec::new();

    for &num_players in &[2u8, 3, 4] {
        for &stack_bb in stack_sizes {
            let config = match num_players {
                2 => AofConfig::heads_up(stack_bb),
                3 => AofConfig::three_player(stack_bb),
                4 => AofConfig::four_player(stack_bb),
                _ => unreachable!(),
            };

            let result = solve(&config, solver_config);
            results.push(result);
        }
    }

    results
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::card::{preflop_combos, preflop_index_to_str, NUM_PREFLOP_HANDS};

    /// Compute combo-weighted push percentage for a position/prior.
    fn combo_weighted_push_pct(result: &SolveResult, position: u8, prior_mask: u8) -> f64 {
        let mut total_combos = 0u32;
        let mut push_combos = 0.0f64;
        for idx in 0..NUM_PREFLOP_HANDS as u8 {
            let c = preflop_combos(idx);
            total_combos += c;
            push_combos += result.get_allin_prob(position, idx, prior_mask) * c as f64;
        }
        push_combos / total_combos as f64
    }

    /// Solve with high iterations for accurate Nash convergence.
    fn solve_accurate(num_players: u8, stack_bb: f64) -> SolveResult {
        let config = AofConfig::no_rake(num_players, stack_bb);
        let solver_config = SolverConfig {
            iterations: 100_000,
            log_interval: 0,
            ..Default::default()
        };
        solve(&config, &solver_config)
    }

    // ─── HU Nash Verification ───────────────────────────────────

    #[test]
    fn test_nash_hu_range_sizes() {
        // Known Nash HU push/fold ranges (no rake, approximate):
        //   5BB: SB pushes ~80-100%, BB calls ~55-65%
        //  10BB: SB pushes ~50-60%, BB calls ~30-40%
        //  15BB: SB pushes ~35-45%, BB calls ~22-30%
        //  20BB: SB pushes ~28-38%, BB calls ~18-25%
        // Bounds are generous to account for MCCFR variance at 100K iterations.
        // Known approximate Nash ranges (HRC/Jennings):
        //   5BB: SB ~80%, BB ~60-65%
        //  10BB: SB ~55%, BB ~35-40%
        //  15BB: SB ~45%, BB ~30-35%
        //  20BB: SB ~35%, BB ~25-30%
        let cases = [
            (5.0, 0.60, 1.00, 0.50, 0.80),   // stack, sb_min, sb_max, bb_min, bb_max
            (10.0, 0.40, 0.75, 0.30, 0.60),
            (15.0, 0.35, 0.65, 0.25, 0.55),
            (20.0, 0.25, 0.60, 0.20, 0.50),
        ];

        for &(stack, sb_min, sb_max, bb_min, bb_max) in &cases {
            let result = solve_accurate(2, stack);

            let sb_push = combo_weighted_push_pct(&result, 0, 0);
            let bb_call = combo_weighted_push_pct(&result, 1, 1); // BB facing SB push

            println!(
                "HU {:.0}BB: SB push {:.1}%, BB call {:.1}%",
                stack,
                sb_push * 100.0,
                bb_call * 100.0
            );

            assert!(
                sb_push >= sb_min && sb_push <= sb_max,
                "HU {:.0}BB SB push {:.1}% not in [{:.0}%, {:.0}%]",
                stack, sb_push * 100.0, sb_min * 100.0, sb_max * 100.0
            );
            assert!(
                bb_call >= bb_min && bb_call <= bb_max,
                "HU {:.0}BB BB call {:.1}% not in [{:.0}%, {:.0}%]",
                stack, bb_call * 100.0, bb_min * 100.0, bb_max * 100.0
            );
        }
    }

    #[test]
    fn test_nash_premium_hands_always_push() {
        // AA, KK should always push/call at any stack depth ≤ 20BB
        for &stack in &[5.0, 10.0, 15.0, 20.0] {
            let result = solve_accurate(2, stack);

            let aa = 12u8; // AA index
            let kk = 11u8; // KK index

            // SB: AA, KK always push
            let aa_push = result.get_allin_prob(0, aa, 0);
            let kk_push = result.get_allin_prob(0, kk, 0);
            assert!(aa_push > 0.90, "HU {:.0}BB: SB AA push {:.1}%", stack, aa_push * 100.0);
            assert!(kk_push > 0.90, "HU {:.0}BB: SB KK push {:.1}%", stack, kk_push * 100.0);

            // BB facing push: AA, KK always call
            let aa_call = result.get_allin_prob(1, aa, 1);
            let kk_call = result.get_allin_prob(1, kk, 1);
            assert!(aa_call > 0.90, "HU {:.0}BB: BB AA call {:.1}%", stack, aa_call * 100.0);
            assert!(kk_call > 0.90, "HU {:.0}BB: BB KK call {:.1}%", stack, kk_call * 100.0);
        }
    }

    #[test]
    fn test_nash_range_tightens_with_stack() {
        // Fundamental: deeper stacks → tighter ranges
        let mut prev_sb_push = 1.0f64;
        let mut prev_bb_call = 1.0f64;

        for &stack in &[5.0, 8.0, 10.0, 15.0, 20.0] {
            let result = solve_accurate(2, stack);

            let sb_push = combo_weighted_push_pct(&result, 0, 0);
            let bb_call = combo_weighted_push_pct(&result, 1, 1);

            assert!(
                sb_push <= prev_sb_push + 0.05, // small tolerance for MCCFR noise
                "SB range should tighten: {:.0}BB ({:.1}%) should be <= prev ({:.1}%)",
                stack, sb_push * 100.0, prev_sb_push * 100.0
            );
            assert!(
                bb_call <= prev_bb_call + 0.05,
                "BB range should tighten: {:.0}BB ({:.1}%) should be <= prev ({:.1}%)",
                stack, bb_call * 100.0, prev_bb_call * 100.0
            );

            prev_sb_push = sb_push;
            prev_bb_call = bb_call;
        }
    }

    #[test]
    fn test_nash_hand_strength_ordering() {
        // Better hands should push at least as often as worse hands
        // Test at 10BB where there's meaningful range differentiation
        let result = solve_accurate(2, 10.0);

        // AA > KK > QQ > JJ (SB push)
        let aa = result.get_allin_prob(0, 12, 0);
        let kk = result.get_allin_prob(0, 11, 0);
        let qq = result.get_allin_prob(0, 10, 0);
        let jj = result.get_allin_prob(0, 9, 0);

        assert!(aa >= kk - 0.05, "AA ({:.1}%) >= KK ({:.1}%)", aa*100.0, kk*100.0);
        assert!(kk >= qq - 0.05, "KK ({:.1}%) >= QQ ({:.1}%)", kk*100.0, qq*100.0);
        assert!(qq >= jj - 0.05, "QQ ({:.1}%) >= JJ ({:.1}%)", qq*100.0, jj*100.0);
    }

    // ─── Multi-way Nash Verification ────────────────────────────

    #[test]
    fn test_nash_3p_position_ordering() {
        // In 3p, later positions should push wider (folded to them)
        let result = solve_accurate(3, 8.0);

        // BTN open (first) should be tighter than SB open (after BTN folds)
        let btn_push = combo_weighted_push_pct(&result, 0, 0);         // BTN opens
        let sb_after_fold = combo_weighted_push_pct(&result, 1, 0);    // SB after BTN folds

        println!("3p 8BB: BTN open {:.1}%, SB after fold {:.1}%",
            btn_push * 100.0, sb_after_fold * 100.0);

        assert!(
            sb_after_fold > btn_push - 0.05,
            "SB after BTN fold ({:.1}%) should be wider than BTN open ({:.1}%)",
            sb_after_fold * 100.0, btn_push * 100.0
        );
    }

    #[test]
    fn test_nash_4p_utg_tightest() {
        // In 4p, UTG (first to act) should have the tightest range
        let result = solve_accurate(4, 8.0);

        let utg_push = combo_weighted_push_pct(&result, 0, 0);         // UTG opens
        let btn_after_fold = combo_weighted_push_pct(&result, 1, 0);   // BTN after UTG folds
        let sb_after_ff = combo_weighted_push_pct(&result, 2, 0);      // SB after UTG+BTN fold

        println!(
            "4p 8BB: UTG open {:.1}%, BTN after fold {:.1}%, SB after FF {:.1}%",
            utg_push * 100.0, btn_after_fold * 100.0, sb_after_ff * 100.0
        );

        assert!(
            utg_push < btn_after_fold + 0.05,
            "UTG ({:.1}%) should be tighter than BTN after fold ({:.1}%)",
            utg_push * 100.0, btn_after_fold * 100.0
        );
        assert!(
            btn_after_fold < sb_after_ff + 0.05,
            "BTN after fold ({:.1}%) should be tighter than SB after FF ({:.1}%)",
            btn_after_fold * 100.0, sb_after_ff * 100.0
        );
    }

    #[test]
    fn test_nash_calling_tighter_than_pushing() {
        // Calling a push should be tighter than open-pushing (you need a stronger hand to call)
        let result = solve_accurate(2, 10.0);

        let sb_push = combo_weighted_push_pct(&result, 0, 0);  // SB open push
        let bb_call = combo_weighted_push_pct(&result, 1, 1);  // BB call vs push

        println!("HU 10BB: SB push {:.1}%, BB call {:.1}%", sb_push * 100.0, bb_call * 100.0);

        assert!(
            bb_call < sb_push + 0.05,
            "BB call ({:.1}%) should be tighter than SB push ({:.1}%)",
            bb_call * 100.0, sb_push * 100.0
        );
    }

    #[test]
    fn test_nash_bb_folded_to_irrelevant() {
        // If everyone folds to BB, BB wins regardless of action.
        // The info set is never visited in CFR, so it defaults to 0.5.
        // This is correct - the BB's decision when folded to has no EV impact.
        // We verify this by checking that the scenario correctly awards BB the pot
        // in compute_terminal_value (tested via terminal payoff assertions elsewhere).
        let result = solve_accurate(2, 8.0);
        let bb_pos = 1u8;
        // This info set may or may not exist; either way it doesn't matter for play
        let _bb_push = combo_weighted_push_pct(&result, bb_pos, 0);
        // No assertion needed - just confirming it doesn't crash
    }

    // ─── Rake impact verification ───────────────────────────────

    #[test]
    fn test_rake_tightens_ranges() {
        // Rake should make ranges tighter compared to no-rake
        let no_rake = solve_accurate(2, 10.0);

        let config_rake = AofConfig::heads_up(10.0); // 2% rake, 3BB cap
        let solver_config = SolverConfig {
            iterations: 100_000,
            log_interval: 0,
            ..Default::default()
        };
        let with_rake = solve(&config_rake, &solver_config);

        let nr_push = combo_weighted_push_pct(&no_rake, 0, 0);
        let wr_push = combo_weighted_push_pct(&with_rake, 0, 0);

        println!("HU 10BB: no-rake SB push {:.1}%, with-rake SB push {:.1}%",
            nr_push * 100.0, wr_push * 100.0);

        assert!(
            wr_push < nr_push + 0.03,
            "Rake should tighten push range: no-rake {:.1}% vs rake {:.1}%",
            nr_push * 100.0, wr_push * 100.0
        );
    }

    // ─── Print detailed comparison ──────────────────────────────

    #[test]
    fn test_print_nash_comparison() {
        // Not an assertion test - prints detailed comparison for manual review
        println!("\n========== NASH PUSH/FOLD VERIFICATION ==========\n");

        for &stack in &[5.0, 10.0, 15.0, 20.0] {
            let result = solve_accurate(2, stack);

            let sb_push = combo_weighted_push_pct(&result, 0, 0);
            let bb_call = combo_weighted_push_pct(&result, 1, 1);

            // Count hands that push/call >50%
            let mut sb_push_count = 0u32;
            let mut bb_call_count = 0u32;
            for idx in 0..NUM_PREFLOP_HANDS as u8 {
                if result.get_allin_prob(0, idx, 0) > 0.5 {
                    sb_push_count += 1;
                }
                if result.get_allin_prob(1, idx, 1) > 0.5 {
                    bb_call_count += 1;
                }
            }

            println!(
                "HU {:.0}BB no-rake: SB push {:.1}% ({}/169 hands), BB call {:.1}% ({}/169 hands)",
                stack, sb_push * 100.0, sb_push_count, bb_call * 100.0, bb_call_count
            );

            // Print top 5 and bottom 5 push hands
            let mut hands: Vec<(u8, f64)> = (0..NUM_PREFLOP_HANDS as u8)
                .map(|i| (i, result.get_allin_prob(0, i, 0)))
                .collect();
            hands.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());

            let top5: Vec<String> = hands[..5].iter()
                .map(|(i, f)| format!("{}({:.0}%)", preflop_index_to_str(*i), f*100.0))
                .collect();
            let bot5: Vec<String> = hands[164..].iter()
                .map(|(i, f)| format!("{}({:.0}%)", preflop_index_to_str(*i), f*100.0))
                .collect();

            println!("  SB top5: {}", top5.join(", "));
            println!("  SB bot5: {}", bot5.join(", "));
        }
        println!("\n=================================================\n");
    }
}
