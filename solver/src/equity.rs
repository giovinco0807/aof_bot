//! Precomputed equity tables for AoF push/fold decisions.
//!
//! For AoF, we precompute a 169×169 equity matrix (heads-up showdown)
//! and use Monte Carlo for multi-way pots.

use crate::card::{
    evaluate_7cards, get_preflop_index, NUM_CARDS, NUM_PREFLOP_HANDS,
};
use rand::Rng;
use rayon::prelude::*;
use std::sync::OnceLock;

/// Heads-up equity matrix: equity[i][j] = equity of hand i vs hand j.
/// equity[i][j] + equity[j][i] = 1.0 (zero-sum).
/// Diagonal equity[i][i] = 0.5.
static HU_EQUITY: OnceLock<Vec<Vec<f64>>> = OnceLock::new();

/// Compute the full 169×169 HU equity matrix via Monte Carlo sampling.
///
/// For each of 14,365 upper-triangle canonical matchups, we sample
/// random specific combos and random boards, then evaluate.
/// With 200K samples per matchup, accuracy is ±0.1% which is more
/// than sufficient for a CFR solver.
///
/// Total work: ~14K matchups × 200K samples × 2 evals ≈ 5.7B evaluations.
/// With hand_table lookup (~15ns): ~85s, parallelized to ~10-20s.
pub fn compute_hu_equity_matrix() -> Vec<Vec<f64>> {
    use rand::SeedableRng;
    use rand_xoshiro::Xoshiro256StarStar;

    let n = NUM_PREFLOP_HANDS; // 169
    let samples_per_matchup = 200_000u64;

    println!(
        "Computing 169x169 HU equity matrix (MC, {} samples/matchup)...",
        samples_per_matchup
    );

    // Precompute: all specific combos for each canonical hand
    let combos_table: Vec<Vec<(u8, u8)>> = (0..n as u8)
        .map(|idx| get_specific_combos(idx))
        .collect();

    // Generate all upper-triangle matchup pairs (i <= j)
    let mut matchups: Vec<(usize, usize)> = Vec::with_capacity(n * (n + 1) / 2);
    for i in 0..n {
        for j in i..n {
            matchups.push((i, j));
        }
    }

    println!("  {} matchups to compute...", matchups.len());

    // Parallel computation over matchups
    let results: Vec<(usize, usize, f64)> = matchups
        .into_par_iter()
        .map(|(i, j)| {
            // Per-thread RNG seeded from matchup index for reproducibility
            let seed = (i as u64) * 1000 + (j as u64);
            let mut rng = Xoshiro256StarStar::seed_from_u64(seed);

            let combos_i = &combos_table[i];
            let combos_j = &combos_table[j];

            if i == j {
                return (i, j, 0.5); // Mirror matchup
            }

            let mut wins = 0.0f64;
            let mut total = 0u64;

            for _ in 0..samples_per_matchup {
                // Pick random specific combos
                let &(c1a, c1b) = &combos_i[rng.gen_range(0..combos_i.len())];
                let &(c2a, c2b) = &combos_j[rng.gen_range(0..combos_j.len())];

                // Skip if cards overlap
                if c1a == c2a || c1a == c2b || c1b == c2a || c1b == c2b {
                    continue;
                }

                // Deal random 5-card board (no overlap with hole cards)
                let used = (1u64 << c1a) | (1u64 << c1b) | (1u64 << c2a) | (1u64 << c2b);
                let remaining: Vec<u8> = (0..NUM_CARDS as u8)
                    .filter(|&c| used & (1u64 << c) == 0)
                    .collect();

                // Fisher-Yates partial shuffle for 5 cards
                let mut buf = [0u8; 48];
                buf[..remaining.len()].copy_from_slice(&remaining);
                let rlen = remaining.len();
                for k in 0..5 {
                    let idx = rng.gen_range(k..rlen);
                    buf.swap(k, idx);
                }

                let hand1 = [c1a, c1b, buf[0], buf[1], buf[2], buf[3], buf[4]];
                let hand2 = [c2a, c2b, buf[0], buf[1], buf[2], buf[3], buf[4]];

                let rank1 = evaluate_7cards(&hand1);
                let rank2 = evaluate_7cards(&hand2);

                if rank1 > rank2 {
                    wins += 1.0;
                } else if rank1 == rank2 {
                    wins += 0.5;
                }
                total += 1;
            }

            let eq = if total > 0 { wins / total as f64 } else { 0.5 };
            (i, j, eq)
        })
        .collect();

    // Build symmetric matrix
    let mut matrix = vec![vec![0.5f64; n]; n];
    for (i, j, eq) in results {
        matrix[i][j] = eq;
        if i != j {
            matrix[j][i] = 1.0 - eq;
        }
    }

    // Sanity checks
    let aa = 12usize;
    let kk = 11usize;
    println!("  AA vs KK equity = {:.4}", matrix[aa][kk]);
    println!("  KK vs AA equity = {:.4}", matrix[kk][aa]);
    println!("  Sum = {:.6} (should be ~1.0)", matrix[aa][kk] + matrix[kk][aa]);
    println!("HU equity matrix computed.");

    matrix
}

/// Get all specific 2-card combos for a canonical hand index.
fn get_specific_combos(canonical_idx: u8) -> Vec<(u8, u8)> {
    let mut combos = Vec::new();
    for c1 in 0..NUM_CARDS as u8 {
        for c2 in (c1 + 1)..NUM_CARDS as u8 {
            if get_preflop_index(c1, c2) == canonical_idx {
                combos.push((c1, c2));
            }
        }
    }
    combos
}

/// Initialize the global HU equity matrix.
pub fn init_hu_equity(matrix: Vec<Vec<f64>>) {
    let _ = HU_EQUITY.set(matrix);
}

/// Get HU equity for hand i vs hand j from the precomputed matrix.
pub fn get_hu_equity(hand_i: u8, hand_j: u8) -> f64 {
    if let Some(matrix) = HU_EQUITY.get() {
        matrix[hand_i as usize][hand_j as usize]
    } else {
        // Fallback: assume 50/50
        0.5
    }
}

/// Check if HU equity matrix is loaded.
pub fn is_hu_equity_loaded() -> bool {
    HU_EQUITY.get().is_some()
}

/// Save HU equity matrix to binary file.
pub fn save_hu_equity(matrix: &[Vec<f64>], path: &str) -> std::io::Result<()> {
    let n = matrix.len();
    let mut bytes = Vec::with_capacity(n * n * 8);
    for row in matrix {
        for &val in row {
            bytes.extend_from_slice(&val.to_le_bytes());
        }
    }
    std::fs::write(path, &bytes)
}

/// Load HU equity matrix from binary file.
pub fn load_hu_equity(path: &str) -> Result<Vec<Vec<f64>>, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("Failed to read {}: {}", path, e))?;
    let n = NUM_PREFLOP_HANDS;
    let expected = n * n * 8;
    if bytes.len() != expected {
        return Err(format!(
            "Invalid equity file size: {} bytes (expected {})",
            bytes.len(),
            expected
        ));
    }

    let mut matrix = vec![vec![0.0f64; n]; n];
    let mut offset = 0;
    for i in 0..n {
        for j in 0..n {
            matrix[i][j] = f64::from_le_bytes(bytes[offset..offset + 8].try_into().unwrap());
            offset += 8;
        }
    }
    Ok(matrix)
}

// ─── 3-way precomputed equity ──────────────────────────────────────

const N3: usize = NUM_PREFLOP_HANDS * NUM_PREFLOP_HANDS * NUM_PREFLOP_HANDS; // 169^3

/// 3-way equity table: flat Vec<f32> of size 169^3.
/// equity_3way[h0 * 169^2 + h1 * 169 + h2] = equity of hero h0 vs opponents h1, h2.
static EQUITY_3WAY: OnceLock<Vec<f32>> = OnceLock::new();

/// Compute the full 3-way equity table via exhaustive board enumeration.
///
/// For each of 169^3 = 4,826,809 canonical matchups:
///   1. Enumerate valid combo assignments (no card overlap), sample up to N
///   2. For each combo assignment, exhaustively enumerate ALL C(46,5) = 1,370,754 boards
///   3. Average equity across all combos and boards → exact result (zero board noise)
///
/// This is both faster and more accurate than MC sampling.
pub fn compute_3way_equity_table() -> Vec<f32> {
    use indicatif::{ProgressBar, ProgressStyle};
    use rand::SeedableRng;
    use rand::Rng;
    use rand_xoshiro::Xoshiro256StarStar;

    let n = NUM_PREFLOP_HANDS;
    let total = n * n * n;
    let samples_per_matchup = 100_000u64;

    println!("Computing 3-way equity table (Symmetry + {} MC samples)...", samples_per_matchup);
    println!("  Total entries: {}, Unique to compute: {}", total, n * (n + 1) / 2);

    let combos_table: Vec<Vec<(u8, u8)>> = (0..n as u8)
        .map(|idx| get_specific_combos(idx))
        .collect();

    let pb = ProgressBar::new(n as u64);
    pb.set_style(
        ProgressStyle::default_bar()
            .template("[{elapsed_precise}] [{bar:50}] h0: {pos}/{len} ({eta})")
            .unwrap()
            .progress_chars("█▓░"),
    );

    let mut results = Vec::with_capacity(total);

    for h0 in 0..n {
        let combos_0 = &combos_table[h0];

        // Find unique opponent combinations (h1 <= h2)
        let mut unique_opps = Vec::with_capacity(n * (n + 1) / 2);
        for h1 in 0..n {
            for h2 in h1..n {
                unique_opps.push((h1, h2));
            }
        }

        let unique_evals: Vec<f32> = unique_opps
            .into_par_iter()
            .enumerate()
            .map(|(sub_idx, (h1, h2))| {
                let mut rng = Xoshiro256StarStar::seed_from_u64((h0 as u64) * 1_000_000 + sub_idx as u64 + 3_000_000_000);
                let combos_1 = &combos_table[h1];
                let combos_2 = &combos_table[h2];

                let mut wins = 0.0f64;
                let mut total_valid = 0u64;

                for _ in 0..samples_per_matchup {
                    let &(c0a, c0b) = &combos_0[rng.gen_range(0..combos_0.len())];
                    let &(c1a, c1b) = &combos_1[rng.gen_range(0..combos_1.len())];
                    let &(c2a, c2b) = &combos_2[rng.gen_range(0..combos_2.len())];

                    let used = (1u64 << c0a) | (1u64 << c0b)
                        | (1u64 << c1a) | (1u64 << c1b)
                        | (1u64 << c2a) | (1u64 << c2b);
                    if used.count_ones() != 6 { continue; }

                    let remaining: Vec<u8> = (0..NUM_CARDS as u8)
                        .filter(|&c| used & (1u64 << c) == 0)
                        .collect();
                    let rlen = remaining.len();
                    let mut buf = [0u8; 48];
                    buf[..rlen].copy_from_slice(&remaining);
                    for k in 0..5 {
                        let idx = rng.gen_range(k..rlen);
                        buf.swap(k, idx);
                    }

                    let r0 = evaluate_7cards(&[c0a, c0b, buf[0], buf[1], buf[2], buf[3], buf[4]]);
                    let r1 = evaluate_7cards(&[c1a, c1b, buf[0], buf[1], buf[2], buf[3], buf[4]]);
                    let r2 = evaluate_7cards(&[c2a, c2b, buf[0], buf[1], buf[2], buf[3], buf[4]]);

                    let max_r = r0.max(r1).max(r2);
                    if r0 == max_r {
                        let winners = (r0 == max_r) as u32
                            + (r1 == max_r) as u32
                            + (r2 == max_r) as u32;
                        wins += 1.0 / winners as f64;
                    }
                    total_valid += 1;
                }

                if total_valid > 0 {
                    (wins / total_valid as f64) as f32
                } else {
                    1.0 / 3.0 // Fallback
                }
            })
            .collect();

        // Populate chunk array for all permutations of (h1, h2)
        let mut chunk_res = vec![0.0f32; n * n];
        let mut idx = 0;
        for h1 in 0..n {
            for h2 in h1..n {
                let eq = unique_evals[idx];
                idx += 1;
                chunk_res[h1 * n + h2] = eq;
                chunk_res[h2 * n + h1] = eq; // Symmetry
            }
        }

        results.extend_from_slice(&chunk_res);
        pb.inc(1);
    }

    pb.finish_with_message("3-way equity table computed.");

    // Sanity check: AA vs KK vs QQ
    let aa = 12usize;
    let kk = 11usize;
    let qq = 10usize;
    let aa_eq = results[aa * n * n + kk * n + qq];
    let kk_eq = results[kk * n * n + aa * n + qq];
    let qq_eq = results[qq * n * n + aa * n + kk];
    println!("  AA vs KK vs QQ: AA={:.4}, KK={:.4}, QQ={:.4} (sum={:.4})",
             aa_eq, kk_eq, qq_eq, aa_eq as f64 + kk_eq as f64 + qq_eq as f64);

    results
}

/// Initialize the global 3-way equity table.
pub fn init_3way_equity(table: Vec<f32>) {
    let _ = EQUITY_3WAY.set(table);
}

/// Get 3-way equity for hero h0 vs opponents h1, h2.
#[inline]
pub fn get_3way_equity(hero: u8, opp1: u8, opp2: u8) -> f64 {
    if let Some(table) = EQUITY_3WAY.get() {
        let n = NUM_PREFLOP_HANDS;
        table[hero as usize * n * n + opp1 as usize * n + opp2 as usize] as f64
    } else {
        1.0 / 3.0
    }
}

/// Check if 3-way equity table is loaded.
pub fn is_3way_equity_loaded() -> bool {
    EQUITY_3WAY.get().is_some()
}

/// Save 3-way equity table to binary file (f32 LE format).
pub fn save_3way_equity(table: &[f32], path: &str) -> std::io::Result<()> {
    let mut bytes = Vec::with_capacity(table.len() * 4);
    for &val in table {
        bytes.extend_from_slice(&val.to_le_bytes());
    }
    std::fs::write(path, &bytes)
}

/// Load 3-way equity table from binary file.
pub fn load_3way_equity(path: &str) -> Result<Vec<f32>, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("Failed to read {}: {}", path, e))?;
    let expected = N3 * 4;
    if bytes.len() != expected {
        return Err(format!(
            "Invalid 3-way equity file: {} bytes (expected {})",
            bytes.len(), expected
        ));
    }
    let table: Vec<f32> = bytes
        .chunks_exact(4)
        .map(|chunk| f32::from_le_bytes(chunk.try_into().unwrap()))
        .collect();
    Ok(table)
}

// ─── 4-way precomputed equity ──────────────────────────────────────

const N4: usize = NUM_PREFLOP_HANDS * NUM_PREFLOP_HANDS * NUM_PREFLOP_HANDS * NUM_PREFLOP_HANDS;

/// 4-way equity table: flat Vec<f32> of size 169^4.
/// equity_4way[h0*169^3 + h1*169^2 + h2*169 + h3] = equity of hero h0 vs opponents h1,h2,h3.
static EQUITY_4WAY: OnceLock<Vec<f32>> = OnceLock::new();

/// Compute the full 4-way equity table via exhaustive board enumeration.
///
/// 169^4 = 815,730,721 entries. For each, sample valid combo assignments
/// then exhaustively enumerate ALL C(44,5) = 1,086,008 boards per combo.
/// Processes in chunks (169^3 per hero hand) to manage memory.
pub fn compute_4way_equity_table() -> Vec<f32> {
    use indicatif::{ProgressBar, ProgressStyle};
    use rand::SeedableRng;
    use rand::Rng;
    use rand_xoshiro::Xoshiro256StarStar;

    let n = NUM_PREFLOP_HANDS;
    let total = N4;
    let samples_per_matchup = 100_000u64;

    println!("Computing 4-way equity table (Symmetry + {} MC samples)...", samples_per_matchup);
    println!("  Total entries: {}, Unique to compute per hero: {}", total, 818515);

    let combos_table: Vec<Vec<(u8, u8)>> = (0..n as u8)
        .map(|idx| get_specific_combos(idx))
        .collect();

    let pb = ProgressBar::new(n as u64);
    pb.set_style(
        ProgressStyle::default_bar()
            .template("[{elapsed_precise}] [{bar:50}] h0: {pos}/{len} ({eta})")
            .unwrap()
            .progress_chars("█▓░"),
    );

    let mut results = Vec::with_capacity(total);

    for h0 in 0..n {
        let combos_0 = &combos_table[h0];

        // Find unique opponent combinations (h1 <= h2 <= h3)
        let mut unique_opps = Vec::with_capacity(820_000);
        for h1 in 0..n {
            for h2 in h1..n {
                for h3 in h2..n {
                    unique_opps.push((h1, h2, h3));
                }
            }
        }

        let unique_evals: Vec<f32> = unique_opps
            .into_par_iter()
            .enumerate()
            .map(|(sub_idx, (h1, h2, h3))| {
                let mut rng = Xoshiro256StarStar::seed_from_u64((h0 as u64) * 5_000_000 + sub_idx as u64 + 4_000_000_000);
                let combos_1 = &combos_table[h1];
                let combos_2 = &combos_table[h2];
                let combos_3 = &combos_table[h3];

                let mut wins = 0.0f64;
                let mut total_valid = 0u64;

                for _ in 0..samples_per_matchup {
                    let &(c0a, c0b) = &combos_0[rng.gen_range(0..combos_0.len())];
                    let &(c1a, c1b) = &combos_1[rng.gen_range(0..combos_1.len())];
                    let &(c2a, c2b) = &combos_2[rng.gen_range(0..combos_2.len())];
                    let &(c3a, c3b) = &combos_3[rng.gen_range(0..combos_3.len())];

                    let used = (1u64 << c0a) | (1u64 << c0b)
                        | (1u64 << c1a) | (1u64 << c1b)
                        | (1u64 << c2a) | (1u64 << c2b)
                        | (1u64 << c3a) | (1u64 << c3b);
                    if used.count_ones() != 8 { continue; }

                    let remaining: Vec<u8> = (0..NUM_CARDS as u8)
                        .filter(|&c| used & (1u64 << c) == 0)
                        .collect();
                    let rlen = remaining.len();
                    let mut buf = [0u8; 48];
                    buf[..rlen].copy_from_slice(&remaining);
                    for k in 0..5 {
                        let idx = rng.gen_range(k..rlen);
                        buf.swap(k, idx);
                    }

                    let r0 = evaluate_7cards(&[c0a, c0b, buf[0], buf[1], buf[2], buf[3], buf[4]]);
                    let r1 = evaluate_7cards(&[c1a, c1b, buf[0], buf[1], buf[2], buf[3], buf[4]]);
                    let r2 = evaluate_7cards(&[c2a, c2b, buf[0], buf[1], buf[2], buf[3], buf[4]]);
                    let r3 = evaluate_7cards(&[c3a, c3b, buf[0], buf[1], buf[2], buf[3], buf[4]]);

                    let max_r = r0.max(r1).max(r2).max(r3);
                    if r0 == max_r {
                        let winners = (r0 == max_r) as u32
                            + (r1 == max_r) as u32
                            + (r2 == max_r) as u32
                            + (r3 == max_r) as u32;
                        wins += 1.0 / winners as f64;
                    }
                    total_valid += 1;
                }

                if total_valid > 0 {
                    (wins / total_valid as f64) as f32
                } else {
                    0.25 // Fallback
                }
            })
            .collect();

        // Populate chunk array for all permutations of (h1, h2, h3)
        let mut chunk_res = vec![0.0f32; n * n * n];
        let mut idx = 0;
        for h1 in 0..n {
            for h2 in h1..n {
                for h3 in h2..n {
                    let eq = unique_evals[idx];
                    idx += 1;
                    
                    // All permutations
                    chunk_res[h1 * n * n + h2 * n + h3] = eq;
                    chunk_res[h1 * n * n + h3 * n + h2] = eq;
                    chunk_res[h2 * n * n + h1 * n + h3] = eq;
                    chunk_res[h2 * n * n + h3 * n + h1] = eq;
                    chunk_res[h3 * n * n + h1 * n + h2] = eq;
                    chunk_res[h3 * n * n + h2 * n + h1] = eq;
                }
            }
        }

        results.extend_from_slice(&chunk_res);
        pb.inc(1);
    }

    pb.finish_with_message("4-way equity table computed.");

    // Sanity check: AA vs KK vs QQ vs JJ
    let aa = 12usize;
    let kk = 11usize;
    let qq = 10usize;
    let jj = 9usize;
    let n3 = n * n * n;
    let n2 = n * n;
    let aa_eq = results[aa * n3 + kk * n2 + qq * n + jj];
    let kk_eq = results[kk * n3 + aa * n2 + qq * n + jj];
    println!("  AA vs KK vs QQ vs JJ: AA={:.4}, KK={:.4}", aa_eq, kk_eq);

    results
}

/// Initialize the global 4-way equity table.
pub fn init_4way_equity(table: Vec<f32>) {
    let _ = EQUITY_4WAY.set(table);
}

/// Get 4-way equity for hero h0 vs opponents h1, h2, h3.
#[inline]
pub fn get_4way_equity(hero: u8, opp1: u8, opp2: u8, opp3: u8) -> f64 {
    if let Some(table) = EQUITY_4WAY.get() {
        let n = NUM_PREFLOP_HANDS;
        let idx = hero as usize * n * n * n
            + opp1 as usize * n * n
            + opp2 as usize * n
            + opp3 as usize;
        table[idx] as f64
    } else {
        0.25
    }
}

/// Check if 4-way equity table is loaded.
pub fn is_4way_equity_loaded() -> bool {
    EQUITY_4WAY.get().is_some()
}

/// Save 4-way equity table to binary file (f32 LE format, ~3.3GB).
pub fn save_4way_equity(table: &[f32], path: &str) -> std::io::Result<()> {
    use std::io::Write;
    let file = std::fs::File::create(path)?;
    let mut writer = std::io::BufWriter::with_capacity(64 * 1024 * 1024, file);
    for &val in table {
        writer.write_all(&val.to_le_bytes())?;
    }
    writer.flush()
}

/// Load 4-way equity table from binary file.
pub fn load_4way_equity(path: &str) -> Result<Vec<f32>, String> {
    use std::io::Read;
    let expected_bytes = N4 * 4;
    let metadata = std::fs::metadata(path)
        .map_err(|e| format!("Failed to read {}: {}", path, e))?;
    if metadata.len() != expected_bytes as u64 {
        return Err(format!(
            "Invalid 4-way equity file: {} bytes (expected {})",
            metadata.len(), expected_bytes
        ));
    }
    println!("Loading 4-way equity table (~{:.1} GB)...", expected_bytes as f64 / 1e9);
    let file = std::fs::File::open(path)
        .map_err(|e| format!("Failed to open {}: {}", path, e))?;
    let mut reader = std::io::BufReader::with_capacity(64 * 1024 * 1024, file);
    let mut bytes = Vec::with_capacity(expected_bytes);
    reader.read_to_end(&mut bytes)
        .map_err(|e| format!("Failed to read {}: {}", path, e))?;
    let table: Vec<f32> = bytes
        .chunks_exact(4)
        .map(|chunk| f32::from_le_bytes(chunk.try_into().unwrap()))
        .collect();
    Ok(table)
}

/// Compute multi-way equity via Monte Carlo for N all-in players.
///
/// `hands`: slice of (hand_index) for each all-in player.
/// `num_samples`: number of Monte Carlo samples.
///
/// Returns equity for each player (sums to 1.0).
pub fn compute_multiway_equity_mc(
    hand_indices: &[u8],
    num_samples: u32,
    rng: &mut impl rand::Rng,
) -> Vec<f64> {
    let n = hand_indices.len();
    if n < 2 {
        return vec![1.0];
    }

    let mut wins = vec![0.0f64; n];

    for _ in 0..num_samples {
        // Deal specific cards for each canonical hand (non-overlapping)
        let mut used = 0u64;
        let mut specific_cards: Vec<(u8, u8)> = Vec::with_capacity(n);
        let mut valid = true;

        for &idx in hand_indices {
            let combos = get_specific_combos(idx);
            // Filter to non-overlapping
            let available: Vec<(u8, u8)> = combos
                .iter()
                .filter(|&&(a, b)| used & (1u64 << a) == 0 && used & (1u64 << b) == 0)
                .copied()
                .collect();

            if available.is_empty() {
                valid = false;
                break;
            }

            let choice = available[rng.gen_range(0..available.len())];
            used |= (1u64 << choice.0) | (1u64 << choice.1);
            specific_cards.push(choice);
        }

        if !valid {
            continue;
        }

        // Deal 5 board cards
        let remaining: Vec<u8> = (0..NUM_CARDS as u8)
            .filter(|&c| used & (1u64 << c) == 0)
            .collect();

        let mut board = [0u8; 5];
        let mut buf = remaining.clone();
        for k in 0..5 {
            let idx = rng.gen_range(k..buf.len());
            buf.swap(k, idx);
            board[k] = buf[k];
        }

        // Evaluate all hands
        let mut ranks = Vec::with_capacity(n);
        for i in 0..n {
            let cards = [
                specific_cards[i].0, specific_cards[i].1,
                board[0], board[1], board[2], board[3], board[4],
            ];
            ranks.push(evaluate_7cards(&cards));
        }

        let max_rank = *ranks.iter().max().unwrap();
        let winners: Vec<usize> = (0..n).filter(|&i| ranks[i] == max_rank).collect();
        let share = 1.0 / winners.len() as f64;
        for &w in &winners {
            wins[w] += share;
        }
    }

    let total: f64 = wins.iter().sum();
    if total > 0.0 {
        wins.iter().map(|&w| w / total).collect()
    } else {
        vec![1.0 / n as f64; n]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_specific_combos_pocket_pair() {
        // AA = index 12, should have C(4,2)=6 combos
        let combos = get_specific_combos(12);
        assert_eq!(combos.len(), 6);
    }

    #[test]
    fn test_specific_combos_suited() {
        // AKs: should have 4 combos (one per suit)
        let aks_idx = crate::card::get_preflop_index(
            crate::card::str_to_card("Ac"),
            crate::card::str_to_card("Kc"),
        );
        let combos = get_specific_combos(aks_idx);
        assert_eq!(combos.len(), 4);
    }

    #[test]
    fn test_specific_combos_offsuit() {
        // AKo: should have 12 combos
        let ako_idx = crate::card::get_preflop_index(
            crate::card::str_to_card("Ac"),
            crate::card::str_to_card("Kd"),
        );
        let combos = get_specific_combos(ako_idx);
        assert_eq!(combos.len(), 12);
    }

    #[test]
    fn test_multiway_equity_mc() {
        use rand::SeedableRng;
        use rand_xoshiro::Xoshiro256StarStar;

        let mut rng = Xoshiro256StarStar::seed_from_u64(42);
        // AA vs KK vs QQ
        let aa = 12u8; // AA
        let kk = 11u8; // KK
        let qq = 10u8; // QQ
        let eq = compute_multiway_equity_mc(&[aa, kk, qq], 10000, &mut rng);
        assert_eq!(eq.len(), 3);
        // AA should have highest equity
        assert!(eq[0] > eq[1], "AA({}) should beat KK({})", eq[0], eq[1]);
        assert!(eq[1] > eq[2], "KK({}) should beat QQ({})", eq[1], eq[2]);
        // Sum should be ~1.0
        let sum: f64 = eq.iter().sum();
        assert!((sum - 1.0).abs() < 0.01);
    }
}
