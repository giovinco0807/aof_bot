//! AoF (All-in or Fold) Solver
//!
//! GTO solver for All-in or Fold poker variant.
//! Supports 2-4 player tables with configurable stack sizes and blind structures.

pub mod card;
pub mod hand_table;
pub mod hand_eval;
pub mod equity;
pub mod aof_state;
pub mod info_set;
pub mod solver;
pub mod charts;
pub mod hand_history;
pub mod opponent_model;
pub mod exploit;
