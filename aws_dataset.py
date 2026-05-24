"""
aws_dataset.py
--------------
DynamoDB integration for the emissions benchmark dataset.

Table schema:
    Partition key : Operation_ID  (String)
    Sort key      : Input_Size_N  (Number)

Environment variables (all optional — fall back to defaults):
    DYNAMODB_TABLE   Table name          (default: carbon_emissions)
    AWS_REGION       AWS region          (default: us-east-1)
    AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY  Standard boto3 credential chain

Usage from carbon_analyzer.py:
    Set DYNAMODB_TABLE env var -> analyzer loads from DynamoDB instead of CSV.

Usage from benchmark_suite.py:
    Set DYNAMODB_TABLE env var -> each benchmark result is also written to DynamoDB.
"""

import csv
import os
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Optional

import boto3
from botocore.exceptions import ClientError

DYNAMODB_TABLE_DEFAULT         = "carbon_emissions"
DYNAMODB_HISTORY_TABLE_DEFAULT = "carbon_emissions_history"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_table(table_name: Optional[str] = None, region: Optional[str] = None):
    table_name = table_name or os.environ.get("DYNAMODB_TABLE", DYNAMODB_TABLE_DEFAULT)
    region = region or os.environ.get("AWS_REGION", "ap-south-1")
    dynamodb = boto3.resource("dynamodb", region_name=region)
    return dynamodb.Table(table_name)


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return Decimal("0")


def _row_to_item(row: dict) -> dict:
    return {
        "Operation_ID":       row["Operation_ID"],
        "Input_Size_N":       int(row["Input_Size_N"]),
        "Equivalence_Group":  row["Equivalence_Group"],
        "Energy_Consumed_kWh": _to_decimal(row["Energy_Consumed_kWh"]),
        "Execution_Time_sec":  _to_decimal(row.get("Execution_Time_sec", 0)),
        "CO2_Emissions_g":    _to_decimal(row["CO2_Emissions_g"]),
        "Repeats_In_Window":  int(row["Repeats_In_Window"]),
        "Run_Mode":           row.get("Run_Mode", "offline"),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_dataset_from_dynamodb(
    table_name: Optional[str] = None,
    region: Optional[str] = None,
) -> dict:
    """
    Scan the DynamoDB table and return the same dict structure as
    carbon_analyzer.load_dataset():  {operation_id: {N: ProfileEntry}}

    Uses a lazy import of ProfileEntry to avoid a circular dependency.
    """
    from carbon_analyzer import ProfileEntry

    table = _get_table(table_name, region)
    items: list[dict] = []

    resp = table.scan()
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))

    db: dict = {}
    for item in items:
        try:
            e = ProfileEntry(
                operation_id=str(item["Operation_ID"]),
                group=str(item["Equivalence_Group"]),
                n=int(item["Input_Size_N"]),
                energy_kwh=float(item["Energy_Consumed_kWh"]),
                co2_g=float(item["CO2_Emissions_g"]),
                repeats=int(item["Repeats_In_Window"]),
            )
            db.setdefault(e.operation_id, {})[e.n] = e
        except (KeyError, ValueError):
            continue

    return db


def write_result_to_dynamodb(
    result_row: dict,
    table_name: Optional[str] = None,
    region: Optional[str] = None,
) -> None:
    """
    Write a single benchmark result row to DynamoDB.
    Called from benchmark_suite.py after each measurement.
    """
    table = _get_table(table_name, region)
    table.put_item(Item=_row_to_item(result_row))


def seed_from_csv(
    csv_path: str,
    table_name: Optional[str] = None,
    region: Optional[str] = None,
) -> int:
    """
    Bulk-upload all rows from the local CSV to DynamoDB using batch_writer.
    Returns the number of rows written.
    Used once (or whenever benchmarks are re-run offline) to bootstrap the table.
    """
    table = _get_table(table_name, region)

    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))

    with table.batch_writer() as batch:
        for row in rows:
            try:
                batch.put_item(Item=_row_to_item(row))
            except (ClientError, ValueError) as exc:
                print(f"  [skip] {row.get('Operation_ID')} N={row.get('Input_Size_N')}: {exc}")

    return len(rows)


def create_table_if_missing(
    table_name: Optional[str] = None,
    region: Optional[str] = None,
) -> str:
    """
    Create the DynamoDB table if it does not already exist.
    Returns 'created' or 'already_exists'.
    """
    table_name = table_name or os.environ.get("DYNAMODB_TABLE", DYNAMODB_TABLE_DEFAULT)
    region = region or os.environ.get("AWS_REGION", "ap-south-1")
    client = boto3.client("dynamodb", region_name=region)

    try:
        client.describe_table(TableName=table_name)
        return "already_exists"
    except client.exceptions.ResourceNotFoundException:
        pass

    client.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "Operation_ID", "KeyType": "HASH"},
            {"AttributeName": "Input_Size_N", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "Operation_ID", "AttributeType": "S"},
            {"AttributeName": "Input_Size_N", "AttributeType": "N"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    waiter = client.get_waiter("table_exists")
    waiter.wait(TableName=table_name)
    return "created"


def create_history_table_if_missing(
    table_name: Optional[str] = None,
    region: Optional[str] = None,
) -> str:
    """
    Create the history table if it does not already exist.
    Schema: PK=Operation_ID (String), SK=Run_SK (String).
    Run_SK format: "{YYYY-MM}#{N zero-padded to 10 digits}"
    e.g. "2024-03#0000100000"
    Lexicographic sort = chronological sort, so latest run = highest SK prefix.
    """
    table_name = table_name or DYNAMODB_HISTORY_TABLE_DEFAULT
    region = region or os.environ.get("AWS_REGION", "ap-south-1")
    client = boto3.client("dynamodb", region_name=region)

    try:
        client.describe_table(TableName=table_name)
        return "already_exists"
    except client.exceptions.ResourceNotFoundException:
        pass

    client.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "Operation_ID", "KeyType": "HASH"},
            {"AttributeName": "Run_SK",       "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "Operation_ID", "AttributeType": "S"},
            {"AttributeName": "Run_SK",       "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=table_name)
    return "created"


def write_run_to_history(
    csv_path: str,
    run_id: str,
    table_name: Optional[str] = None,
    region: Optional[str] = None,
) -> int:
    """
    Write all rows from csv_path into the history table tagged with run_id.
    run_id should be "YYYY-MM" (e.g. "2024-03").
    Returns the number of rows written.
    If the run_id already exists in the table the rows are overwritten (idempotent).
    """
    table_name = table_name or DYNAMODB_HISTORY_TABLE_DEFAULT
    region = region or os.environ.get("AWS_REGION", "ap-south-1")
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(table_name)

    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))

    written = 0
    with table.batch_writer() as batch:
        for row in rows:
            try:
                n = int(row["Input_Size_N"])
                run_sk = f"{run_id}#{n:010d}"
                item = _row_to_item(row)
                item["Run_SK"]  = run_sk
                item["Run_ID"]  = run_id
                # History table uses Run_SK as sort key, not Input_Size_N (Number).
                # Remove the original numeric sort key to avoid schema mismatch.
                item.pop("Input_Size_N", None)
                item["Input_Size_N"] = n   # store as regular attribute (Number via int)
                batch.put_item(Item=item)
                written += 1
            except (ClientError, ValueError, KeyError) as exc:
                print(f"  [skip history] {row.get('Operation_ID')} N={row.get('Input_Size_N')}: {exc}")

    return written


def update_live_from_history(
    table_name_history: Optional[str] = None,
    table_name_live: Optional[str] = None,
    keep_runs: int = 3,
    region: Optional[str] = None,
) -> dict:
    """
    1. Scan the history table.
    2. For each (Operation_ID, Input_Size_N), collect all run values.
    3. Keep only the `keep_runs` most recent run_ids.
    4. Average CO2 and energy across those runs.
    5. Write averaged rows back to the live table.
    6. Delete history rows whose run_id fell outside the window.

    Returns a summary dict: {kept_runs: [...], deleted_runs: [...], rows_updated: int}
    """
    table_name_history = table_name_history or DYNAMODB_HISTORY_TABLE_DEFAULT
    table_name_live    = table_name_live    or DYNAMODB_TABLE_DEFAULT
    region             = region or os.environ.get("AWS_REGION", "ap-south-1")
    dynamodb = boto3.resource("dynamodb", region_name=region)
    hist_table = dynamodb.Table(table_name_history)
    live_table = dynamodb.Table(table_name_live)

    # --- Scan history ---
    items = []
    resp = hist_table.scan()
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = hist_table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))

    # --- Group by (Operation_ID, N) then by Run_ID ---
    # Structure: groups[(op_id, n)][run_id] = item
    groups: dict = defaultdict(dict)
    for item in items:
        op_id  = item["Operation_ID"]
        n      = int(item["Input_Size_N"])
        run_id = item["Run_ID"]
        groups[(op_id, n)][run_id] = item

    # --- Determine which run_ids to keep globally ---
    all_run_ids = sorted({item["Run_ID"] for item in items})  # lexicographic = chronological
    kept_runs    = all_run_ids[-keep_runs:]
    deleted_runs = all_run_ids[:-keep_runs]

    # --- Write averaged rows to live table ---
    rows_updated = 0
    with live_table.batch_writer() as batch:
        for (op_id, n), run_map in groups.items():
            valid_items = [v for k, v in run_map.items() if k in kept_runs]
            if not valid_items:
                continue

            avg_co2    = sum(float(i["CO2_Emissions_g"])    for i in valid_items) / len(valid_items)
            avg_energy = sum(float(i["Energy_Consumed_kWh"]) for i in valid_items) / len(valid_items)
            avg_time   = sum(float(i.get("Execution_Time_sec", 0)) for i in valid_items) / len(valid_items)
            sample     = valid_items[0]

            batch.put_item(Item={
                "Operation_ID":       op_id,
                "Input_Size_N":       n,
                "Equivalence_Group":  sample["Equivalence_Group"],
                "Energy_Consumed_kWh": _to_decimal(avg_energy),
                "Execution_Time_sec":  _to_decimal(avg_time),
                "CO2_Emissions_g":    _to_decimal(avg_co2),
                "Repeats_In_Window":  int(sample["Repeats_In_Window"]),
                "Run_Mode":           sample.get("Run_Mode", "offline"),
            })
            rows_updated += 1

    # --- Delete old history rows ---
    with hist_table.batch_writer() as batch:
        for item in items:
            if item["Run_ID"] in deleted_runs:
                batch.delete_item(Key={
                    "Operation_ID": item["Operation_ID"],
                    "Run_SK":       item["Run_SK"],
                })

    return {
        "kept_runs":    kept_runs,
        "deleted_runs": deleted_runs,
        "rows_updated": rows_updated,
    }
