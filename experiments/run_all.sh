#!/usr/bin/env bash
# Runs all experiments one after another: 3 modes x 3 congestion levels.
#
# Usage (from the repo root):
#     bash experiments/run_all.sh
#
# Takes about 12 minutes. Enter your sudo password once at the start and do not
# touch the terminal while it runs. Results are collected in results/summary.csv
# (one row per severity per run).
set -u
cd "$(dirname "$0")/.." || exit 1

RYU="$HOME/ryu39/bin/ryu-manager"
DURATION=30                  # seconds the clients keep sending
WAIT=$((DURATION + 35))      # total time per run (server stays up longer than clients)

if [ ! -x "$RYU" ]; then
    echo "ryu-manager not found at $RYU (is the ryu39 environment installed?)"
    exit 1
fi

sudo -v || exit 1
mkdir -p results/raw

# keep any earlier manual results instead of mixing them with this clean set
if [ -f results/summary.csv ]; then
    mv results/summary.csv "results/summary_manual_$(date +%H%M%S).csv"
fi

run_one() {
    local mode=$1 cond=$2 rate=$3
    local label="${mode}_${cond}"
    echo "=== running $label ==="
    sudo -v

    rm -f results/sent_*.csv results/received.csv results/server.out
    sudo mn -c > /dev/null 2>&1

    # controller in the chosen mode, with its own port-stats file for this run
    STATS_FILE="results/port_stats_${label}.csv" LOG_MODE="$mode" \
        "$RYU" controller/log_controller.py > "results/controller_${label}.log" 2>&1 &
    local ryu_pid=$!
    sleep 5

    # network + server + clients + background traffic, driven from Python
    sudo python3 experiments/run_experiment.py --rate "$rate" \
        --duration "$DURATION" --wait "$WAIT" > "results/mininet_${label}.log" 2>&1

    kill "$ryu_pid" 2>/dev/null
    wait "$ryu_pid" 2>/dev/null

    sudo chown -R "$USER" results
    mkdir -p "results/raw/$label"
    cp results/sent_*.csv results/received.csv "results/raw/$label/" 2>/dev/null
    python3 app/analyze.py --label "$label"
    echo
}

for entry in none:0 moderate:1500K heavy:3M; do
    cond="${entry%%:*}"
    rate="${entry##*:}"
    for mode in baseline static dynamic; do
        run_one "$mode" "$cond" "$rate"
    done
done

sudo mn -c > /dev/null 2>&1
echo "All done. Full table: results/summary.csv"
column -s, -t results/summary.csv 2>/dev/null || cat results/summary.csv
