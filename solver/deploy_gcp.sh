#!/bin/bash
sudo apt-get update
sudo apt-get install -y unzip tmux
rm -rf ~/aof_solver
mkdir ~/aof_solver
mv ~/solver_src.zip ~/aof_solver/
cd ~/aof_solver
unzip -o solver_src.zip
source $HOME/.cargo/env

# Build only main to avoid api_server errors (missing static files)
cargo build --release --bin main

# Start computations in tmux
tmux new-session -d -s compute_3way 'source $HOME/.cargo/env && cargo run --release --bin main -- precompute --type 3way -o data/equity_3way_exact.bin 2>&1 | tee 3way.log'
tmux new-session -d -s compute_4way 'source $HOME/.cargo/env && cargo run --release --bin main -- precompute --type 4way -o data/equity_4way_exact.bin 2>&1 | tee 4way.log'

echo "Started 3-way and 4-way tmux sessions on GCP."
