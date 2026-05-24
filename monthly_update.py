"""
monthly_update.py
-----------------
Runs once a month (triggered by EventBridge → EC2).

Steps:
  1. Run benchmark_suite.py  →  fresh emissions_dataset.csv
  2. Write the new run to carbon_emissions_history (tagged with YYYY-MM)
  3. Average last 3 runs  →  overwrite carbon_emissions (live table)
  4. Delete history rows older than the 3rd most recent run
  5. (On EC2) shut the instance down to stop billing

Usage:
    python monthly_update.py [--mode offline|online] [--no-shutdown]

Set env vars before running:
    DYNAMODB_TABLE          (default: carbon_emissions)
    DYNAMODB_HISTORY_TABLE  (default: carbon_emissions_history)
    AWS_REGION              (default: ap-south-1)
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY  or use IAM role
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone

from aws_dataset import (
    DYNAMODB_TABLE_DEFAULT,
    DYNAMODB_HISTORY_TABLE_DEFAULT,
    create_table_if_missing,
    create_history_table_if_missing,
    write_run_to_history,
    update_live_from_history,
)

HERE    = os.path.dirname(os.path.abspath(__file__))
CSV_OUT = os.path.join(HERE, "emissions_dataset.csv")


def run_benchmarks(mode: str) -> None:
    print(f"\n[1/4] Running benchmark_suite.py --mode {mode} ...")
    result = subprocess.run(
        [sys.executable, os.path.join(HERE, "benchmark_suite.py"), "--mode", mode],
        cwd=HERE,
    )
    if result.returncode != 0:
        raise RuntimeError(f"benchmark_suite.py exited with code {result.returncode}")
    if not os.path.isfile(CSV_OUT):
        raise RuntimeError(f"Expected output CSV not found: {CSV_OUT}")
    print(f"    Benchmarks complete. CSV at: {CSV_OUT}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Monthly benchmark update pipeline")
    parser.add_argument("--mode",        choices=["offline", "online"], default="offline")
    parser.add_argument("--no-shutdown", action="store_true", help="Skip EC2 self-shutdown (for local testing)")
    parser.add_argument("--keep-runs",   type=int, default=3, help="Sliding window size (default 3)")
    args = parser.parse_args()

    table_live    = os.environ.get("DYNAMODB_TABLE",         DYNAMODB_TABLE_DEFAULT)
    table_history = os.environ.get("DYNAMODB_HISTORY_TABLE", DYNAMODB_HISTORY_TABLE_DEFAULT)
    region        = os.environ.get("AWS_REGION",             "ap-south-1")

    run_id = datetime.now(timezone.utc).strftime("%Y-%m")   # e.g. "2024-03"
    print(f"Monthly update  |  run_id={run_id}  |  mode={args.mode}")

    # -- Step 1: run benchmarks ------------------------------------------------
    run_benchmarks(args.mode)

    # -- Step 2: ensure tables exist -------------------------------------------
    print("\n[2/4] Ensuring DynamoDB tables exist ...")
    status_live    = create_table_if_missing(table_live, region)
    status_history = create_history_table_if_missing(table_history, region)
    print(f"    {table_live}    -> {status_live}")
    print(f"    {table_history} -> {status_history}")

    # -- Step 3: write new run to history table --------------------------------
    print(f"\n[3/4] Writing run '{run_id}' to history table ...")
    written = write_run_to_history(CSV_OUT, run_id, table_history, region)
    print(f"    {written} rows written to history")

    # -- Step 4: average + update live + cleanup --------------------------------
    print(f"\n[4/4] Averaging last {args.keep_runs} runs → updating live table ...")
    summary = update_live_from_history(table_history, table_live, args.keep_runs, region)
    print(f"    Kept runs    : {summary['kept_runs']}")
    print(f"    Deleted runs : {summary['deleted_runs']}")
    print(f"    Rows updated : {summary['rows_updated']}")

    print("\nMonthly update complete.")

    # -- EC2 self-shutdown (skipped locally) ------------------------------------
    if not args.no_shutdown:
        print("Shutting down EC2 instance ...")
        os.system("sudo shutdown -h now")


if __name__ == "__main__":
    main()
