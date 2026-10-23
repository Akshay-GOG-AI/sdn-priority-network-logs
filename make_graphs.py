#!/usr/bin/env python3
"""
Generate graphs for the SDN-based priority handling of network logs project.

Expected summary file:
  results/summary.csv
with columns similar to:
  label,severity,sent,received,loss_pct,avg_delay_ms,p95_delay_ms,max_delay_ms

Outputs:
  results/graphs/critical_average_delay.png
  results/graphs/critical_p95_delay.png
  results/graphs/average_delay_by_severity.png
  results/graphs/bottleneck_utilization_*.png (when port-stat data is recognizable)

Run from the project root:
  python3 make_graphs.py

This script does not modify the input CSV files.
"""

from pathlib import Path
import re
import sys

try:
    import pandas as pd
    import matplotlib.pyplot as plt
except ImportError as exc:
    print(f"Missing Python package: {exc}")
    print("Install the required packages with: python3 -m pip install pandas matplotlib")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
GRAPHS = RESULTS / "graphs"
SUMMARY = RESULTS / "summary.csv"

MODES = ["baseline", "static", "dynamic"]
CONDITIONS = ["none", "moderate", "heavy"]
SEVERITIES = ["CRITICAL", "ERROR", "WARNING", "INFO"]


def clean_label(value):
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def parse_label(label):
    """Extract mode and condition from labels such as baseline_heavy."""
    normalized = clean_label(label)
    mode = next((m for m in MODES if normalized.startswith(m + "_") or normalized == m), None)
    condition = next((c for c in CONDITIONS if re.search(rf"(^|_){c}($|_)", normalized)), None)
    return mode, condition


def load_summary():
    if not SUMMARY.exists():
        print(f"ERROR: Cannot find {SUMMARY}")
        print("Place make_graphs.py in the project root beside the results folder.")
        sys.exit(1)

    df = pd.read_csv(SUMMARY)
    df.columns = [str(c).strip().lower() for c in df.columns]
    required = {"label", "severity", "avg_delay_ms", "p95_delay_ms"}
    missing = required - set(df.columns)
    if missing:
        print("ERROR: summary.csv is missing required columns:", ", ".join(sorted(missing)))
        print("Columns found:", ", ".join(df.columns))
        sys.exit(1)

    df["label"] = df["label"].astype(str).str.strip()
    df["severity"] = df["severity"].astype(str).str.strip().str.upper()
    parsed = df["label"].apply(parse_label)
    df["mode"] = parsed.apply(lambda x: x[0])
    df["condition"] = parsed.apply(lambda x: x[1])
    df["avg_delay_ms"] = pd.to_numeric(df["avg_delay_ms"], errors="coerce")
    df["p95_delay_ms"] = pd.to_numeric(df["p95_delay_ms"], errors="coerce")
    if "loss_pct" in df.columns:
        df["loss_pct"] = pd.to_numeric(df["loss_pct"], errors="coerce")
    return df


def latest_rows(df):
    """
    If summary.csv contains repeated runs, retain the last row for each
    label+severity combination based on CSV row order. This avoids mixing
    appended historical runs into one plotted result.
    """
    key = ["label", "severity"]
    duplicates = int(df.duplicated(key, keep=False).sum())
    if duplicates:
        print(f"NOTE: Found repeated label/severity rows ({duplicates} rows involved).")
        print("Using the LAST row for each label + severity, based on CSV order.")
        print("If rows are not chronologically appended, verify the CSV manually.")
    return df.drop_duplicates(key, keep="last").copy()


def save_bar(data, x, y, title, ylabel, filename, hue=None, order=None):
    if data.empty:
        print(f"SKIP: No data for {filename}")
        return
    plt.figure(figsize=(10, 5.8))
    if hue:
        pivot = data.pivot(index=x, columns=hue, values=y)
        if order:
            pivot = pivot.reindex([item for item in order if item in pivot.index])
        pivot.plot(kind="bar", ax=plt.gca())
        plt.legend(title=hue.replace("_", " ").title())
    else:
        data = data.copy()
        if order:
            data[x] = pd.Categorical(data[x], categories=order, ordered=True)
            data = data.sort_values(x)
        plt.bar(data[x].astype(str), data[y])
    plt.title(title)
    plt.xlabel(x.replace("_", " ").title())
    plt.ylabel(ylabel)
    plt.xticks(rotation=0 if len(data) <= 6 else 20, ha="right" if len(data) > 6 else "center")
    plt.grid(axis="y", linestyle="--", alpha=0.35)
    plt.tight_layout()
    path = GRAPHS / filename
    plt.savefig(path, dpi=180, bbox_inches="tight")
    plt.close()
    print("Saved:", path.relative_to(ROOT))


def make_critical_graph(df, metric, title, ylabel, filename):
    data = df[df["severity"] == "CRITICAL"].dropna(subset=[metric]).copy()
    data = data[data["mode"].notna() & data["condition"].notna()]
    if data.empty:
        print(f"SKIP: No valid CRITICAL data for {filename}")
        return
    data["experiment"] = data["condition"] + " / " + data["mode"]
    order = [f"{c} / {m}" for c in CONDITIONS for m in MODES]
    save_bar(data, "experiment", metric, title, ylabel, filename, order=order)


def make_severity_graph(df):
    # Heavy congestion is the most informative condition for showing severity differentiation.
    data = df[(df["condition"] == "heavy") & df["mode"].notna()].copy()
    data = data.dropna(subset=["avg_delay_ms"])
    if data.empty:
        # Fall back to all valid records if labels don't parse as expected.
        data = df[df["mode"].notna() & df["condition"].notna()].dropna(subset=["avg_delay_ms"]).copy()
    if data.empty:
        print("SKIP: No valid data for average_delay_by_severity.png")
        return
    # Make one grouped bar chart per severity with each mode/condition represented.
    data["group"] = data["severity"] + " / " + data["mode"]
    data = data.groupby(["severity", "mode"], as_index=False)["avg_delay_ms"].mean()
    data["group"] = data["severity"] + " / " + data["mode"]
    save_bar(
        data, "group", "avg_delay_ms",
        "Average delay by log severity (heavy congestion)",
        "Average delay (ms)", "average_delay_by_severity.png",
        order=[f"{s} / {m}" for s in SEVERITIES for m in MODES]
    )


def find_column(columns, candidates):
    for candidate in candidates:
        for col in columns:
            if candidate in col.lower().replace(" ", "_"):
                return col
    return None


def make_utilization_graphs():
    files = sorted(RESULTS.glob("port_stats_*.csv"))
    if not files:
        print("NOTE: No results/port_stats_*.csv files found; skipping bottleneck utilization graph.")
        return

    for file in files:
        try:
            stats = pd.read_csv(file)
        except Exception as exc:
            print(f"SKIP: Could not read {file.name}: {exc}")
            continue
        stats.columns = [str(c).strip().lower() for c in stats.columns]
        if stats.empty:
            print(f"SKIP: {file.name} is empty")
            continue

        time_col = find_column(stats.columns, ["time", "timestamp", "second", "elapsed"])
        util_col = find_column(stats.columns, ["utilization_pct", "utilization_percent", "util_pct", "utilization"])
        if util_col:
            y = pd.to_numeric(stats[util_col], errors="coerce")
            ylabel = "Bottleneck utilization (%)" if "pct" in util_col or "percent" in util_col else util_col.replace("_", " ").title()
        else:
            # If the file stores byte counters, compute approximate Mbps from successive samples.
            tx_col = find_column(stats.columns, ["tx_bytes", "bytes_tx", "transmitted_bytes", "txbytes"])
            rx_col = find_column(stats.columns, ["rx_bytes", "bytes_rx", "received_bytes", "rxbytes"])
            byte_col = tx_col or rx_col
            if not byte_col or not time_col:
                print(f"NOTE: Can't identify time/utilization/byte columns in {file.name}; skipping it.")
                print("       Columns:", ", ".join(stats.columns))
                continue
            t = pd.to_numeric(stats[time_col], errors="coerce")
            b = pd.to_numeric(stats[byte_col], errors="coerce")
            dt = t.diff()
            db = b.diff()
            y = (db * 8 / dt / 1_000_000).where((dt > 0) & (db >= 0))
            ylabel = "Approximate throughput (Mbps)"
        if time_col:
            x = pd.to_numeric(stats[time_col], errors="coerce")
            x_label = time_col.replace("_", " ").title()
        else:
            x = pd.Series(range(len(stats)), index=stats.index)
            x_label = "Sample"
        valid = pd.DataFrame({"x": x, "y": y}).dropna()
        if valid.empty:
            print(f"SKIP: No plottable values in {file.name}")
            continue
        plt.figure(figsize=(10, 5.8))
        plt.plot(valid["x"], valid["y"], marker="o", markersize=2, linewidth=1.5)
        plt.title(f"Bottleneck statistics: {file.stem.replace('_', ' ')}")
        plt.xlabel(x_label)
        plt.ylabel(ylabel)
        plt.grid(True, linestyle="--", alpha=0.35)
        plt.tight_layout()
        out = GRAPHS / f"{file.stem}.png"
        plt.savefig(out, dpi=180, bbox_inches="tight")
        plt.close()
        print("Saved:", out.relative_to(ROOT))


def main():
    GRAPHS.mkdir(parents=True, exist_ok=True)
    df = load_summary()
    df = latest_rows(df)

    make_critical_graph(
        df, "avg_delay_ms",
        "Average delay of CRITICAL logs",
        "Average delay (ms)", "critical_average_delay.png"
    )
    make_critical_graph(
        df, "p95_delay_ms",
        "P95 delay of CRITICAL logs",
        "P95 delay (ms)", "critical_p95_delay.png"
    )
    make_severity_graph(df)
    make_utilization_graphs()

    print("\nGraph generation finished.")
    print(f"Output folder: {GRAPHS}")
    print("Review the figures and verify that the selected CSV rows correspond to your intended experiment run.")


if __name__ == "__main__":
    main()
