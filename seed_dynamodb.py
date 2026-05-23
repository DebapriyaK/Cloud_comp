"""
seed_dynamodb.py
----------------
One-time (or re-run) script to:
  1. Create the DynamoDB table if it doesn't exist.
  2. Upload all rows from emissions_dataset.csv.

Usage:
    python seed_dynamodb.py [--table carbon_emissions] [--region us-east-1] [--csv emissions_dataset.csv]

AWS credentials are picked up from the standard boto3 chain:
    - env vars AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
    - ~/.aws/credentials
    - IAM role (on EC2/ECS/EKS)
"""

import argparse
import os
import sys

from aws_dataset import create_table_if_missing, seed_from_csv, DYNAMODB_TABLE_DEFAULT

DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emissions_dataset.csv")


def main():
    parser = argparse.ArgumentParser(description="Seed DynamoDB with emissions benchmark data")
    parser.add_argument("--table",  default=DYNAMODB_TABLE_DEFAULT, help="DynamoDB table name")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--csv",    default=DEFAULT_CSV, help="Path to emissions_dataset.csv")
    args = parser.parse_args()

    if not os.path.isfile(args.csv):
        print(f"Error: CSV not found at {args.csv}")
        sys.exit(1)

    print(f"Table  : {args.table}")
    print(f"Region : {args.region}")
    print(f"CSV    : {args.csv}")
    print()

    print("Step 1: Ensuring table exists...")
    status = create_table_if_missing(args.table, args.region)
    print(f"  -> {status}")

    print("Step 2: Uploading rows...")
    count = seed_from_csv(args.csv, args.table, args.region)
    print(f"  -> {count} rows written")

    print("\nDone. Set DYNAMODB_TABLE={} to use this table.".format(args.table))


if __name__ == "__main__":
    main()
