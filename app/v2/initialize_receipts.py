"""Explicit provisioning: python -m app.v2.initialize_receipts ABSOLUTE_DB_PATH."""

import argparse
from pathlib import Path

from app.v2.receipts import SQLiteReceiptStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    SQLiteReceiptStore(args.path, lock_timeout_seconds=0.25).initialize()
    print("v2 receipt store initialized; existing records preserved")


if __name__ == "__main__":
    main()
