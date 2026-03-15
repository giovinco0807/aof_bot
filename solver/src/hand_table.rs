//! 7-card hand lookup table using combinatorial number system indexing.
//!
//! Pre-generates all C(52,7) = 133,784,560 hand evaluations into a ~255MB table.
//! Replaces 21× evaluate_5cards per hand with a single table read (~20x speedup).

use std::sync::OnceLock;

const fn compute_binom() -> [[u32; 8]; 53] {
    let mut t = [[0u32; 8]; 53];
    let mut n = 0usize;
    while n < 53 {
        t[n][0] = 1;
        let mut k = 1usize;
        while k < 8 && k <= n {
            t[n][k] = t[n - 1][k - 1] + t[n - 1][k];
            k += 1;
        }
        n += 1;
    }
    t
}

const BINOM: [[u32; 8]; 53] = compute_binom();

pub const TOTAL_COMBOS: usize = 133_784_560;

#[inline]
fn comb_index_sorted(sorted: &[u8; 7]) -> usize {
    BINOM[sorted[0] as usize][1] as usize
        + BINOM[sorted[1] as usize][2] as usize
        + BINOM[sorted[2] as usize][3] as usize
        + BINOM[sorted[3] as usize][4] as usize
        + BINOM[sorted[4] as usize][5] as usize
        + BINOM[sorted[5] as usize][6] as usize
        + BINOM[sorted[6] as usize][7] as usize
}

static TABLE: OnceLock<Vec<u16>> = OnceLock::new();

pub fn init(path: &str) -> Result<(), String> {
    let bytes = std::fs::read(path).map_err(|e| format!("Failed to read {}: {}", path, e))?;
    if bytes.len() != TOTAL_COMBOS * 2 {
        return Err(format!(
            "Invalid table size: {} bytes (expected {})",
            bytes.len(),
            TOTAL_COMBOS * 2
        ));
    }
    let table: Vec<u16> = bytes
        .chunks_exact(2)
        .map(|c| u16::from_le_bytes([c[0], c[1]]))
        .collect();
    TABLE
        .set(table)
        .map_err(|_| "Table already initialized".to_string())
}

pub fn init_from_vec(table: Vec<u16>) -> Result<(), String> {
    TABLE
        .set(table)
        .map_err(|_| "Table already initialized".to_string())
}

pub fn is_loaded() -> bool {
    TABLE.get().is_some()
}

#[inline]
pub fn lookup(cards: &[u8]) -> Option<u16> {
    let table = TABLE.get()?;
    let mut sorted = [
        cards[0], cards[1], cards[2], cards[3], cards[4], cards[5], cards[6],
    ];
    sorted.sort_unstable();
    Some(table[comb_index_sorted(&sorted)])
}

pub fn generate() -> Vec<u16> {
    use std::collections::BTreeSet;

    println!("  Step 1: Building rank mapping from C(52,5) hands...");
    let mut score_set = BTreeSet::new();
    for a in 0..48u8 {
        for b in (a + 1)..49 {
            for c in (b + 1)..50 {
                for d in (c + 1)..51 {
                    for e in (d + 1)..52 {
                        score_set.insert(crate::card::evaluate_5cards(&[a, b, c, d, e]));
                    }
                }
            }
        }
    }
    let sorted_scores: Vec<u64> = score_set.into_iter().collect();
    println!("  {} unique hand ranks", sorted_scores.len());

    use std::collections::HashMap;
    let score_to_rank: HashMap<u64, u16> = sorted_scores
        .iter()
        .enumerate()
        .map(|(i, &s)| (s, i as u16))
        .collect();

    println!(
        "  Step 2: Evaluating {} 7-card combinations...",
        TOTAL_COMBOS
    );
    let mut table = vec![0u16; TOTAL_COMBOS];
    let mut count = 0u64;

    for a in 0..46u8 {
        for b in (a + 1)..47 {
            for c in (b + 1)..48 {
                for d in (c + 1)..49 {
                    for e in (d + 1)..50 {
                        for f in (e + 1)..51 {
                            for g in (f + 1)..52 {
                                let cards = [a, b, c, d, e, f, g];
                                let score = crate::card::evaluate_7cards_direct(&cards);
                                let rank = score_to_rank[&score];
                                let idx = comb_index_sorted(&cards);
                                table[idx] = rank;
                                count += 1;
                                if count % 10_000_000 == 0 {
                                    println!(
                                        "    {:.1}% ({}/{})",
                                        count as f64 / TOTAL_COMBOS as f64 * 100.0,
                                        count,
                                        TOTAL_COMBOS
                                    );
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    println!("  Done: {} entries generated", count);
    table
}

pub fn save(table: &[u16], path: &str) -> std::io::Result<()> {
    let mut bytes = Vec::with_capacity(table.len() * 2);
    for &v in table {
        bytes.extend_from_slice(&v.to_le_bytes());
    }
    std::fs::write(path, &bytes)
}
