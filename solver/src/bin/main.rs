//! AoF Solver CLI
//!
//! Commands:
//!   solve   - Solve AoF for specific configurations
//!   export  - Export charts to JSON

use aof_solver::aof_state::AofConfig;
use aof_solver::charts;
use aof_solver::equity;
use aof_solver::hand_table;
use aof_solver::solver::{self, SolverConfig};
use clap::{Parser, Subcommand};

const HAND_TABLE_PATH: &str = "data/hand_table.bin";

#[derive(Parser)]
#[command(name = "aof-solver", about = "AoF (All-in or Fold) GTO Solver")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Solve AoF for specific configurations
    Solve {
        /// Number of players (2, 3, or 4)
        #[arg(short = 'n', long, default_value = "4")]
        num_players: u8,

        /// Stack size in BB
        #[arg(short = 's', long, default_value = "8.0")]
        stack: f64,

        /// Rake percentage (e.g. 0.02 for 2%)
        #[arg(long, default_value = "0.02")]
        rake: f64,

        /// Rake cap in BB
        #[arg(long, default_value = "3.0")]
        rake_cap: f64,

        /// Number of CFR iterations
        #[arg(short = 'i', long, default_value = "100000")]
        iterations: usize,

        /// Monte Carlo samples for multi-way equity
        #[arg(long, default_value = "10000")]
        mc_samples: u32,

        /// Number of boards to sample at multi-way showdowns (1=fast/noisy, 1000=slower/clean)
        #[arg(long, default_value = "1")]
        board_samples: u32,

        /// Output JSON file path
        #[arg(short = 'o', long)]
        output: Option<String>,

        /// Path to HU equity matrix file (loads if exists, computes if not)
        #[arg(long, default_value = "data/equity_hu.bin")]
        equity_path: String,

        /// Path to 3-way equity table file (optional, enables noise-free 3-way showdowns)
        #[arg(long)]
        equity_3way_path: Option<String>,

        /// Path to 4-way equity table file (optional, enables noise-free 4-way showdowns)
        #[arg(long)]
        equity_4way_path: Option<String>,
    },

    /// Solve all standard configurations (2p, 3p, 4p × multiple stacks)
    SolveAll {
        /// Number of CFR iterations
        #[arg(short = 'i', long, default_value = "100000")]
        iterations: usize,

        /// Output directory for charts
        #[arg(short = 'o', long, default_value = "data/charts")]
        output_dir: String,

        /// Path to HU equity matrix file
        #[arg(long, default_value = "data/equity_hu.bin")]
        equity_path: String,

        /// Rake percentage (e.g. 0.02 for 2%)
        #[arg(long, default_value = "0.0")]
        rake: f64,

        /// Rake cap in BB
        #[arg(long, default_value = "0.0")]
        rake_cap: f64,
    },

    /// Precompute equity tables (hu, 3way, 4way)
    Precompute {
        /// Type of equity to compute: hu, 3way, 4way
        #[arg(short = 't', long = "type", default_value = "hu")]
        equity_type: String,

        /// Output path for equity file
        #[arg(short = 'o', long)]
        output: Option<String>,
    },
}

fn main() {
    let cli = Cli::parse();

    match cli.command {
        Commands::Solve {
            num_players,
            stack,
            rake,
            rake_cap,
            iterations,
            mc_samples,
            board_samples,
            output,
            equity_path,
            equity_3way_path,
            equity_4way_path,
        } => {
            load_or_generate_hand_table();
            load_or_compute_equity(&equity_path);

            // Load multi-way equity tables if provided
            if let Some(path) = &equity_3way_path {
                load_3way_equity_file(path);
            }
            if let Some(path) = &equity_4way_path {
                load_4way_equity_file(path);
            }

            let mut config = match num_players {
                2 => AofConfig::heads_up(stack),
                3 => AofConfig::three_player(stack),
                4 => AofConfig::four_player(stack),
                _ => {
                    eprintln!("Error: num_players must be 2, 3, or 4");
                    std::process::exit(1);
                }
            };
            config.rake_pct = rake;
            config.rake_cap_bb = rake_cap;

            let solver_config = SolverConfig {
                iterations,
                mc_samples,
                log_interval: iterations / 10,
                board_samples,
                ..Default::default()
            };

            let result = solver::solve(&config, &solver_config);
            let chart_set = charts::generate_charts(&result);
            charts::print_summary(&chart_set);

            if let Some(path) = output {
                charts::export_json(&chart_set, &path).expect("Failed to write JSON");
                println!("Charts exported to {}", path);
            }
        }

        Commands::SolveAll {
            iterations,
            output_dir,
            equity_path,
            rake,
            rake_cap,
        } => {
            load_or_generate_hand_table();
            load_or_compute_equity(&equity_path);
            std::fs::create_dir_all(&output_dir).expect("Failed to create output directory");

            let stack_sizes = vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0, 20.0];
            let solver_config = SolverConfig {
                iterations,
                mc_samples: 10_000,
                log_interval: iterations / 10,
                ..Default::default()
            };

            let rake_suffix = if rake > 0.0 {
                format!("_rake{:.0}pct", rake * 100.0)
            } else {
                String::new()
            };

            for &num_players in &[2u8, 3, 4] {
                for &stack_bb in &stack_sizes {
                    let mut config = match num_players {
                        2 => AofConfig::heads_up(stack_bb),
                        3 => AofConfig::three_player(stack_bb),
                        4 => AofConfig::four_player(stack_bb),
                        _ => unreachable!(),
                    };
                    config.rake_pct = rake;
                    config.rake_cap_bb = rake_cap;

                    let result = solver::solve(&config, &solver_config);
                    let chart_set = charts::generate_charts(&result);
                    charts::print_summary(&chart_set);

                    let filename = format!(
                        "{}/aof_{}p_{:.0}bb{}.json",
                        output_dir, num_players, stack_bb, rake_suffix
                    );
                    charts::export_json(&chart_set, &filename).expect("Failed to write JSON");
                    println!("Exported: {}", filename);
                }
            }
        }

        Commands::Precompute { equity_type, output } => {
            // Load hand table for ~20x faster evaluation
            load_or_generate_hand_table();

            match equity_type.as_str() {
                "hu" => {
                    let path = output.unwrap_or_else(|| "data/equity_hu.bin".to_string());
                    if let Some(parent) = std::path::Path::new(&path).parent() {
                        std::fs::create_dir_all(parent).expect("Failed to create directory");
                    }
                    let matrix = equity::compute_hu_equity_matrix();
                    equity::save_hu_equity(&matrix, &path).expect("Failed to save");
                    println!("HU equity matrix saved to {}", path);
                }
                "3way" => {
                    let path = output.unwrap_or_else(|| "data/equity_3way.bin".to_string());
                    if let Some(parent) = std::path::Path::new(&path).parent() {
                        std::fs::create_dir_all(parent).expect("Failed to create directory");
                    }
                    let table = equity::compute_3way_equity_table();
                    equity::save_3way_equity(&table, &path).expect("Failed to save");
                    println!("3-way equity table saved to {} ({:.1} MB)", path, table.len() as f64 * 4.0 / 1e6);
                }
                "4way" => {
                    let path = output.unwrap_or_else(|| "data/equity_4way.bin".to_string());
                    if let Some(parent) = std::path::Path::new(&path).parent() {
                        std::fs::create_dir_all(parent).expect("Failed to create directory");
                    }
                    let table = equity::compute_4way_equity_table();
                    equity::save_4way_equity(&table, &path).expect("Failed to save");
                    println!("4-way equity table saved to {} ({:.1} GB)", path, table.len() as f64 * 4.0 / 1e9);
                }
                _ => {
                    eprintln!("Error: --type must be hu, 3way, or 4way");
                    std::process::exit(1);
                }
            }
        }
    }
}

/// Load or generate the 7-card hand lookup table (~255MB).
/// This provides a ~20x speedup for hand evaluation.
fn load_or_generate_hand_table() {
    if hand_table::is_loaded() {
        return;
    }

    if std::path::Path::new(HAND_TABLE_PATH).exists() {
        println!("Loading hand table from {}...", HAND_TABLE_PATH);
        match hand_table::init(HAND_TABLE_PATH) {
            Ok(()) => {
                println!("Hand table loaded.");
                return;
            }
            Err(e) => {
                eprintln!("Warning: Failed to load hand table: {}", e);
            }
        }
    }

    println!("Generating 7-card hand table (this takes a few minutes, ~255MB)...");
    let table = hand_table::generate();

    if let Some(parent) = std::path::Path::new(HAND_TABLE_PATH).parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let bytes: Vec<u8> = table.iter().flat_map(|&v| v.to_le_bytes()).collect();
    match std::fs::write(HAND_TABLE_PATH, &bytes) {
        Ok(()) => println!("Hand table saved to {}", HAND_TABLE_PATH),
        Err(e) => eprintln!("Warning: Failed to save hand table: {}", e),
    }

    // Initialize from the generated table directly
    let _ = hand_table::init_from_vec(table);
}

fn load_3way_equity_file(path: &str) {
    if std::path::Path::new(path).exists() {
        println!("Loading 3-way equity table from {}...", path);
        match equity::load_3way_equity(path) {
            Ok(table) => {
                println!("3-way equity table loaded ({:.1} MB).", table.len() as f64 * 4.0 / 1e6);
                equity::init_3way_equity(table);
            }
            Err(e) => {
                eprintln!("Warning: Failed to load 3-way equity: {}", e);
            }
        }
    } else {
        eprintln!("Warning: 3-way equity file not found: {}", path);
        eprintln!("  Run: cargo run --release --bin main -- precompute --type 3way");
    }
}

fn load_4way_equity_file(path: &str) {
    if std::path::Path::new(path).exists() {
        println!("Loading 4-way equity table from {}...", path);
        match equity::load_4way_equity(path) {
            Ok(table) => {
                println!("4-way equity table loaded ({:.1} GB).", table.len() as f64 * 4.0 / 1e9);
                equity::init_4way_equity(table);
            }
            Err(e) => {
                eprintln!("Warning: Failed to load 4-way equity: {}", e);
            }
        }
    } else {
        eprintln!("Warning: 4-way equity file not found: {}", path);
        eprintln!("  Run: cargo run --release --bin main -- precompute --type 4way");
    }
}

fn load_or_compute_equity(path: &str) {
    if std::path::Path::new(path).exists() {
        println!("Loading HU equity matrix from {}...", path);
        match equity::load_hu_equity(path) {
            Ok(matrix) => {
                equity::init_hu_equity(matrix);
                println!("HU equity matrix loaded.");
            }
            Err(e) => {
                eprintln!("Warning: Failed to load equity matrix: {}", e);
                eprintln!("Computing fresh matrix...");
                let matrix = equity::compute_hu_equity_matrix();
                equity::init_hu_equity(matrix);
            }
        }
    } else {
        println!("HU equity matrix not found, computing (this may take a while)...");
        let matrix = equity::compute_hu_equity_matrix();

        if let Some(parent) = std::path::Path::new(path).parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Err(e) = equity::save_hu_equity(&matrix, path) {
            eprintln!("Warning: Failed to save equity matrix: {}", e);
        } else {
            println!("HU equity matrix saved to {}", path);
        }

        equity::init_hu_equity(matrix);
    }
}
