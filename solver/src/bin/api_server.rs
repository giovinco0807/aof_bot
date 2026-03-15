//! HTTP API server for real-time AoF decisions.
//!
//! Endpoints:
//!   GET  /gto         - Get GTO decision
//!   GET  /exploit     - Get exploitative decision
//!   POST /record_hand - Record a hand history
//!   GET  /player_stats - Get player statistics
//!   GET  /health      - Health check

use aof_solver::aof_state::AofConfig;
use aof_solver::card::{get_preflop_index, str_to_card};
use aof_solver::equity;
use aof_solver::exploit;
use aof_solver::hand_history::{HandHistoryDb, HandRecord};
use aof_solver::hand_table;
use aof_solver::opponent_model::OpponentRange;
use aof_solver::solver::{self, SolveResult, SolverConfig};

use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::Json,
    routing::{get, post},
    Router,
};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use tower_http::cors::CorsLayer;
use tower_http::services::ServeDir;

/// Shared application state.
struct AppState {
    /// Pre-solved GTO strategies: (num_players, stack_bb_rounded) -> SolveResult
    strategies: HashMap<(u8, u32), SolveResult>,
    /// Hand history database
    db: Mutex<HandHistoryDb>,
    /// Default game config
    default_config: AofConfig,
}

#[derive(Deserialize)]
struct GtoQuery {
    /// Hand string, e.g. "AcKd"
    hand: String,
    /// Number of players (2, 3, or 4)
    num_players: u8,
    /// Stack size in BB
    stack: f64,
    /// Prior actions string, e.g. "FA" (pos0=Fold, pos1=AllIn)
    #[serde(default)]
    prior_actions: String,
}

#[derive(Deserialize)]
struct ExploitQuery {
    hand: String,
    num_players: u8,
    stack: f64,
    #[serde(default)]
    prior_actions: String,
    /// Comma-separated player IDs for opponents
    #[serde(default)]
    player_ids: String,
}

#[derive(Deserialize)]
struct PlayerStatsQuery {
    id: String,
}

#[derive(Serialize)]
struct GtoResponse {
    hand: String,
    position: u8,
    prior_actions: String,
    allin_probability: f64,
    action: String,
}

#[derive(Serialize)]
struct ExploitResponse {
    hand: String,
    gto_allin: f64,
    exploit_allin: f64,
    blended_allin: f64,
    action: String,
    confidence: f64,
}

#[derive(Serialize)]
struct PlayerStatsResponse {
    player_id: String,
    total_hands: u32,
    vpip: f64,
    allin_count: u32,
    fold_count: u32,
    showdown_count: u32,
    confidence: f64,
}

#[derive(Serialize)]
struct HealthResponse {
    status: String,
    strategies_loaded: usize,
}

#[tokio::main]
async fn main() {
    println!("Starting AoF API Server...");

    // Load hand table for fast evaluation
    let hand_table_path = "data/hand_table.bin";
    if std::path::Path::new(hand_table_path).exists() {
        println!("Loading hand table from {}...", hand_table_path);
        hand_table::init(hand_table_path).expect("Failed to load hand table");
        println!("Hand table loaded.");
    } else {
        eprintln!("Warning: hand_table.bin not found, using slow evaluation");
    }

    // Load or compute equity
    let equity_path = "data/equity_hu.bin";
    load_or_compute_equity(equity_path);

    // Pre-solve strategies for common configurations
    let solver_config = SolverConfig {
        iterations: 100_000_000,
        mc_samples: 5_000,
        log_interval: 10_000_000,
        ..Default::default()
    };

    let mut strategies = HashMap::new();
    let stack_sizes = vec![3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0, 20.0];

    for &n in &[2u8, 3, 4] {
        for &s in &stack_sizes {
            let config = make_config(n, s);
            println!("Pre-solving {}p {:.0}BB...", n, s);
            let result = solver::solve(&config, &solver_config);
            strategies.insert((n, (s * 10.0) as u32), result);
        }
    }

    println!("Pre-solved {} configurations.", strategies.len());

    // Open database
    let db_path = "data/history/hands.db";
    std::fs::create_dir_all("data/history").expect("Failed to create data directory");
    let db = HandHistoryDb::open(db_path).expect("Failed to open database");

    let state = Arc::new(AppState {
        strategies,
        db: Mutex::new(db),
        default_config: AofConfig::four_player(8.0),
    });

    let app = Router::new()
        .route("/health", get(health))
        .route("/gto", get(gto_decision))
        .route("/exploit", get(exploit_decision))
        .route("/record_hand", post(record_hand))
        .route("/player_stats", get(player_stats))
        .nest_service("/charts", ServeDir::new("data/charts"))
        .route("/viewer", get(serve_viewer))
        .route("/", get(serve_viewer))
        .layer(CorsLayer::permissive())
        .with_state(state);

    let addr = "0.0.0.0:8080";
    println!("API server listening on {}", addr);

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}

async fn serve_viewer() -> axum::response::Html<&'static str> {
    axum::response::Html(include_str!("../../static/viewer.html"))
}

async fn health(State(state): State<Arc<AppState>>) -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "ok".to_string(),
        strategies_loaded: state.strategies.len(),
    })
}

async fn gto_decision(
    State(state): State<Arc<AppState>>,
    Query(query): Query<GtoQuery>,
) -> Result<Json<GtoResponse>, (StatusCode, String)> {
    let (c1, c2) = parse_hand(&query.hand)?;
    let hand_index = get_preflop_index(c1, c2);
    let position = query.prior_actions.len() as u8;
    let prior_mask = parse_prior_actions(&query.prior_actions);

    let key = (query.num_players, (query.stack * 10.0) as u32);
    let result = state.strategies.get(&key)
        .ok_or((StatusCode::NOT_FOUND, format!("No strategy for {}p {:.1}BB", query.num_players, query.stack)))?;

    let allin_prob = result.get_allin_prob(position, hand_index, prior_mask);

    Ok(Json(GtoResponse {
        hand: query.hand,
        position,
        prior_actions: query.prior_actions,
        allin_probability: allin_prob,
        action: if allin_prob > 0.5 { "AllIn".to_string() } else { "Fold".to_string() },
    }))
}

async fn exploit_decision(
    State(state): State<Arc<AppState>>,
    Query(query): Query<ExploitQuery>,
) -> Result<Json<ExploitResponse>, (StatusCode, String)> {
    let (c1, c2) = parse_hand(&query.hand)?;
    let hand_index = get_preflop_index(c1, c2);
    let position = query.prior_actions.len() as u8;
    let prior_mask = parse_prior_actions(&query.prior_actions);

    let key = (query.num_players, (query.stack * 10.0) as u32);
    let gto_result = state.strategies.get(&key)
        .ok_or((StatusCode::NOT_FOUND, format!("No strategy for {}p {:.1}BB", query.num_players, query.stack)))?;

    // Build opponent ranges from database (for EV calculation if needed, though we moved to pure 1% shift)
    let player_ids: Vec<&str> = query.player_ids.split(',').filter(|s| !s.is_empty()).collect();
    let mut opponent_ranges = Vec::new();
    let mut avg_confidence = 0.0f64;

    let db = state.db.lock().map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    for pid in &player_ids {
        if let Ok(Some(stats)) = db.get_player_stats(pid) {
            avg_confidence += stats.confidence();
            opponent_ranges.push(OpponentRange::from_stats(&stats));
        } else {
            opponent_ranges.push(OpponentRange::uniform());
        }
    }
    drop(db);

    let confidence = if player_ids.is_empty() {
        0.0
    } else {
        avg_confidence / player_ids.len() as f64
    };

    // Load profiles (fast enough to read from local SSD on each request for a single bot)
    let mut profiles: aof_solver::exploit::ProfilesMap = std::collections::HashMap::new();
    if let Ok(json_str) = std::fs::read_to_string("data/opponent_profiles.json") {
        if let Ok(parsed) = serde_json::from_str(&json_str) {
            profiles = parsed;
        }
    }

    let config = make_config(query.num_players, query.stack);
    let decision = exploit::compute_exploit_decision(
        gto_result,
        hand_index,
        position,
        prior_mask,
        &query.prior_actions,
        &player_ids,
        &config,
        &profiles,
    );

    Ok(Json(ExploitResponse {
        hand: query.hand,
        gto_allin: decision.gto_allin,
        exploit_allin: decision.exploit_allin,
        blended_allin: decision.blended_allin,
        action: format!("{:?}", decision.action),
        confidence,
    }))
}

async fn record_hand(
    State(state): State<Arc<AppState>>,
    Json(record): Json<HandRecord>,
) -> Result<Json<serde_json::Value>, (StatusCode, String)> {
    let db = state.db.lock().map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let id = db.record_hand(&record).map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e))?;
    Ok(Json(serde_json::json!({"id": id, "status": "ok"})))
}

async fn player_stats(
    State(state): State<Arc<AppState>>,
    Query(query): Query<PlayerStatsQuery>,
) -> Result<Json<PlayerStatsResponse>, (StatusCode, String)> {
    let db = state.db.lock().map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let stats = db.get_player_stats(&query.id)
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e))?
        .ok_or((StatusCode::NOT_FOUND, format!("Player {} not found", query.id)))?;

    let vpip = stats.vpip();
    let confidence = stats.confidence();
    Ok(Json(PlayerStatsResponse {
        player_id: stats.player_id,
        total_hands: stats.total_hands,
        vpip,
        allin_count: stats.allin_count,
        fold_count: stats.fold_count,
        showdown_count: stats.showdown_count,
        confidence,
    }))
}

fn parse_hand(hand_str: &str) -> Result<(u8, u8), (StatusCode, String)> {
    if hand_str.len() != 4 {
        return Err((StatusCode::BAD_REQUEST, format!("Invalid hand: {}", hand_str)));
    }
    let c1 = str_to_card(&hand_str[0..2]);
    let c2 = str_to_card(&hand_str[2..4]);
    Ok((c1, c2))
}

fn parse_prior_actions(actions: &str) -> u8 {
    let mut mask = 0u8;
    for (i, ch) in actions.chars().enumerate() {
        if ch == 'A' {
            mask |= 1 << i;
        }
    }
    mask
}

fn make_config(num_players: u8, stack_bb: f64) -> AofConfig {
    let mut config = match num_players {
        2 => AofConfig::heads_up(stack_bb),
        3 => AofConfig::three_player(stack_bb),
        4 => AofConfig::four_player(stack_bb),
        _ => AofConfig::four_player(stack_bb),
    };
    
    // Set Effective AoF Rake to 1.0% with a 1.5BB cap (2% base minus 50% rakeback)
    config.rake_pct = 0.01;
    config.rake_cap_bb = 1.5;
    
    config
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
                eprintln!("Warning: {}", e);
                compute_and_save_equity(path);
            }
        }
    } else {
        compute_and_save_equity(path);
    }
}

fn compute_and_save_equity(path: &str) {
    println!("Computing HU equity matrix...");
    let matrix = equity::compute_hu_equity_matrix();
    if let Some(parent) = std::path::Path::new(path).parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Err(e) = equity::save_hu_equity(&matrix, path) {
        eprintln!("Warning: Failed to save: {}", e);
    }
    equity::init_hu_equity(matrix);
}
