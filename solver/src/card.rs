//! Card representation and hand evaluation.
//!
//! Cards are integers 0-51:
//!   rank = card / 4   (0=2, 1=3, ..., 12=Ace)
//!   suit = card % 4   (0=Clubs, 1=Diamonds, 2=Hearts, 3=Spades)
//!
//! Hand evaluation returns an integer score (higher = stronger).

pub const NUM_CARDS: usize = 52;
pub const NUM_RANKS: usize = 13;
pub const NUM_SUITS: usize = 4;

pub const RANKS_STR: &str = "23456789TJQKA";
pub const SUITS_STR: &str = "cdhs";

// ─── Card utilities ─────────────────────────────────────────────

#[inline]
pub fn rank(card: u8) -> u8 {
    card / 4
}

#[inline]
pub fn suit(card: u8) -> u8 {
    card % 4
}

pub fn card_to_str(card: u8) -> String {
    let r = RANKS_STR.as_bytes()[rank(card) as usize] as char;
    let s = SUITS_STR.as_bytes()[suit(card) as usize] as char;
    format!("{}{}", r, s)
}

pub fn str_to_card(s: &str) -> u8 {
    let bytes = s.as_bytes();
    let r = RANKS_STR.find(bytes[0] as char).expect("invalid rank") as u8;
    let s = SUITS_STR.find(bytes[1] as char).expect("invalid suit") as u8;
    r * 4 + s
}

pub fn cards_to_str(cards: &[u8]) -> String {
    cards
        .iter()
        .map(|&c| card_to_str(c))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Bitmask-based 5-card hand evaluator.
/// Flush: suit equality check.  Straight: 13-bit rank mask vs 10 patterns.
/// Hand type: rank count array (no sorting needed).
#[inline]
pub fn evaluate_5cards(cards: &[u8; 5]) -> u64 {
    let r0 = (cards[0] >> 2) as usize;
    let r1 = (cards[1] >> 2) as usize;
    let r2 = (cards[2] >> 2) as usize;
    let r3 = (cards[3] >> 2) as usize;
    let r4 = (cards[4] >> 2) as usize;

    let s0 = cards[0] & 3;
    let s1 = cards[1] & 3;
    let s2 = cards[2] & 3;
    let s3 = cards[3] & 3;
    let s4 = cards[4] & 3;

    let is_flush = (s0 == s1) & (s1 == s2) & (s2 == s3) & (s3 == s4);

    let rank_mask: u16 =
        (1u16 << r0) | (1u16 << r1) | (1u16 << r2) | (1u16 << r3) | (1u16 << r4);

    const STRAIGHTS: [(u16, u8); 10] = [
        (0x1F00, 12),
        (0x0F80, 11),
        (0x07C0, 10),
        (0x03E0, 9),
        (0x01F0, 8),
        (0x00F8, 7),
        (0x007C, 6),
        (0x003E, 5),
        (0x001F, 4),
        (0x100F, 3),
    ];

    let mut is_straight = false;
    let mut straight_high: u8 = 0;
    let unique_count = rank_mask.count_ones();
    if unique_count == 5 {
        for &(pat, high) in &STRAIGHTS {
            if rank_mask == pat {
                is_straight = true;
                straight_high = high;
                break;
            }
        }
    }

    let mut rc = [0u8; 13];
    rc[r0] += 1;
    rc[r1] += 1;
    rc[r2] += 1;
    rc[r3] += 1;
    rc[r4] += 1;

    let mut max_cnt: u8 = 0;
    let mut max_rank: u8 = 0;
    let mut sec_cnt: u8 = 0;
    let mut sec_rank: u8 = 0;

    for r in (0..13u8).rev() {
        let c = rc[r as usize];
        if c == 0 {
            continue;
        }
        if c > max_cnt || (c == max_cnt && max_cnt == 0) {
            sec_cnt = max_cnt;
            sec_rank = max_rank;
            max_cnt = c;
            max_rank = r;
        } else if c > sec_cnt {
            sec_cnt = c;
            sec_rank = r;
        }
    }

    const CAT: u64 = 1_000_000;
    const B: u64 = 13;

    if is_straight && is_flush {
        return 8 * CAT + straight_high as u64;
    }
    if max_cnt == 4 {
        let kicker = find_kicker_4(rc, max_rank);
        return 7 * CAT + max_rank as u64 * B + kicker as u64;
    }
    if max_cnt == 3 && sec_cnt == 2 {
        return 6 * CAT + max_rank as u64 * B + sec_rank as u64;
    }
    if is_flush {
        return 5 * CAT + encode_mask_desc(rank_mask);
    }
    if is_straight {
        return 4 * CAT + straight_high as u64;
    }
    if max_cnt == 3 {
        let (k1, k2) = find_kickers_3(rc, max_rank);
        return 3 * CAT + max_rank as u64 * B * B + k1 as u64 * B + k2 as u64;
    }
    if max_cnt == 2 && sec_cnt == 2 {
        let (hi_pair, lo_pair) = if max_rank > sec_rank {
            (max_rank, sec_rank)
        } else {
            (sec_rank, max_rank)
        };
        let kicker = find_kicker_2pair(rc, hi_pair, lo_pair);
        return 2 * CAT + hi_pair as u64 * B * B + lo_pair as u64 * B + kicker as u64;
    }
    if max_cnt == 2 {
        let (k1, k2, k3) = find_kickers_pair(rc, max_rank);
        return 1 * CAT
            + max_rank as u64 * B * B * B
            + k1 as u64 * B * B
            + k2 as u64 * B
            + k3 as u64;
    }

    encode_mask_desc(rank_mask)
}

#[inline(always)]
fn encode_mask_desc(mask: u16) -> u64 {
    const B: u64 = 13;
    let mut val = 0u64;
    let mut m = mask;
    let n = m.count_ones() as u32;
    let mut i = 0u32;
    while m != 0 {
        let bit = 15 - m.leading_zeros();
        val += (bit as u64) * B.pow(n - 1 - i);
        m &= !(1u16 << bit);
        i += 1;
    }
    val
}

#[inline(always)]
fn find_kicker_4(rc: [u8; 13], quads: u8) -> u8 {
    for r in (0..13u8).rev() {
        if r != quads && rc[r as usize] > 0 {
            return r;
        }
    }
    0
}

#[inline(always)]
fn find_kickers_3(rc: [u8; 13], trips: u8) -> (u8, u8) {
    let mut k1 = 0u8;
    let mut found = false;
    for r in (0..13u8).rev() {
        if r != trips && rc[r as usize] > 0 {
            if !found {
                k1 = r;
                found = true;
            } else {
                return (k1, r);
            }
        }
    }
    (k1, 0)
}

#[inline(always)]
fn find_kicker_2pair(rc: [u8; 13], hi: u8, lo: u8) -> u8 {
    for r in (0..13u8).rev() {
        if r != hi && r != lo && rc[r as usize] > 0 {
            return r;
        }
    }
    0
}

#[inline(always)]
fn find_kickers_pair(rc: [u8; 13], pair: u8) -> (u8, u8, u8) {
    let mut kickers = [0u8; 3];
    let mut ki = 0;
    for r in (0..13u8).rev() {
        if r != pair && rc[r as usize] > 0 {
            kickers[ki] = r;
            ki += 1;
            if ki == 3 {
                break;
            }
        }
    }
    (kickers[0], kickers[1], kickers[2])
}

// ─── 7-card hand evaluation ────────────────────────────────────

/// Evaluate 7 cards by trying all C(7,5)=21 combinations.
/// Uses lookup table if loaded, falls back to direct.
#[inline]
pub fn evaluate_7cards(cards: &[u8]) -> u64 {
    if let Some(rank) = crate::hand_table::lookup(cards) {
        return rank as u64;
    }
    evaluate_7cards_direct(cards)
}

/// Direct 7-card evaluation via C(7,5)=21 calls to evaluate_5cards.
pub fn evaluate_7cards_direct(cards: &[u8]) -> u64 {
    assert!(cards.len() >= 7, "need at least 7 cards");
    let mut best = 0u64;
    for i in 0..7 {
        for j in (i + 1)..7 {
            for k in (j + 1)..7 {
                for l in (k + 1)..7 {
                    for m in (l + 1)..7 {
                        let five = [cards[i], cards[j], cards[k], cards[l], cards[m]];
                        let score = evaluate_5cards(&five);
                        if score > best {
                            best = score;
                        }
                    }
                }
            }
        }
    }
    best
}

// ─── Preflop canonical ─────────────────────────────────────────

/// Number of canonical preflop hand types.
pub const NUM_PREFLOP_HANDS: usize = 169;

/// Returns a preflop canonical index 0-168.
///   0-12:   pocket pairs
///   13-90:  suited combos
///   91-168: offsuit combos
pub fn get_preflop_index(card1: u8, card2: u8) -> u8 {
    let mut r1 = rank(card1);
    let mut r2 = rank(card2);
    let s1 = suit(card1);
    let s2 = suit(card2);

    if r1 < r2 {
        std::mem::swap(&mut r1, &mut r2);
    }

    if r1 == r2 {
        return r1;
    }

    if s1 == s2 {
        let mut idx: u8 = 13;
        for i in 1u8..13 {
            for j in 0..i {
                if i == r1 && j == r2 {
                    return idx;
                }
                idx += 1;
            }
        }
    } else {
        let mut idx: u8 = 91;
        for i in 1u8..13 {
            for j in 0..i {
                if i == r1 && j == r2 {
                    return idx;
                }
                idx += 1;
            }
        }
    }

    0
}

/// Returns a preflop canonical string like "AKs", "TTo", "72o".
pub fn get_preflop_canonical(card1: u8, card2: u8) -> String {
    let mut r1 = rank(card1);
    let mut r2 = rank(card2);
    let s1 = suit(card1);
    let s2 = suit(card2);

    if r1 < r2 {
        std::mem::swap(&mut r1, &mut r2);
    }

    let ranks_bytes = RANKS_STR.as_bytes();
    let c1 = ranks_bytes[r1 as usize] as char;
    let c2 = ranks_bytes[r2 as usize] as char;

    if r1 == r2 {
        format!("{}{}", c1, c2)
    } else if s1 == s2 {
        format!("{}{}s", c1, c2)
    } else {
        format!("{}{}o", c1, c2)
    }
}

/// Returns the canonical string for a preflop index 0-168.
pub fn preflop_index_to_str(idx: u8) -> String {
    let ranks_bytes = RANKS_STR.as_bytes();
    if idx <= 12 {
        // Pocket pair
        let c = ranks_bytes[idx as usize] as char;
        format!("{}{}", c, c)
    } else if idx <= 90 {
        // Suited
        let mut n = idx - 13;
        for i in 1u8..13 {
            for j in 0..i {
                if n == 0 {
                    let c1 = ranks_bytes[i as usize] as char;
                    let c2 = ranks_bytes[j as usize] as char;
                    return format!("{}{}s", c1, c2);
                }
                n -= 1;
            }
        }
        unreachable!()
    } else {
        // Offsuit
        let mut n = idx - 91;
        for i in 1u8..13 {
            for j in 0..i {
                if n == 0 {
                    let c1 = ranks_bytes[i as usize] as char;
                    let c2 = ranks_bytes[j as usize] as char;
                    return format!("{}{}o", c1, c2);
                }
                n -= 1;
            }
        }
        unreachable!()
    }
}

/// Returns the number of specific combos for a canonical hand type.
/// Pocket pairs: 6 combos, suited: 4 combos, offsuit: 12 combos
pub fn preflop_combos(idx: u8) -> u32 {
    if idx <= 12 {
        6
    } else if idx <= 90 {
        4
    } else {
        12
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rank_suit() {
        assert_eq!(rank(0), 0);
        assert_eq!(suit(0), 0);
        assert_eq!(rank(51), 12);
        assert_eq!(suit(51), 3);
    }

    #[test]
    fn test_card_str_roundtrip() {
        for c in 0..52u8 {
            assert_eq!(str_to_card(&card_to_str(c)), c);
        }
    }

    #[test]
    fn test_preflop_index_range() {
        for c1 in 0..52u8 {
            for c2 in (c1 + 1)..52 {
                let idx = get_preflop_index(c1, c2);
                assert!(idx < 169, "idx {} out of range for {} {}", idx, c1, c2);
            }
        }
    }

    #[test]
    fn test_preflop_index_to_str() {
        assert_eq!(preflop_index_to_str(12), "AA");
        assert_eq!(preflop_index_to_str(0), "22");
    }

    #[test]
    fn test_hand_ordering() {
        let high_card = evaluate_5cards(&[48, 45, 42, 36, 31]);
        let one_pair = evaluate_5cards(&[48, 49, 44, 42, 37]);
        let flush = evaluate_5cards(&[48, 40, 32, 24, 16]);
        let sf = evaluate_5cards(&[48, 44, 40, 36, 32]);
        assert!(high_card < one_pair);
        assert!(one_pair < flush);
        assert!(flush < sf);
    }
}
