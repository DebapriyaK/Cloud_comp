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
from decimal import Decimal, InvalidOperation
from typing import Optional

import boto3
from botocore.exceptions import ClientError

DYNAMODB_TABLE_DEFAULT = "carbon_emissions"


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
