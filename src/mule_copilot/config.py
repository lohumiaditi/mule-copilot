import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_PROCESSED = ROOT / "data" / "processed"
EVALS = ROOT / "evals"

TRANSACTIONS = DATA_PROCESSED / "transactions.parquet"
LABELS = DATA_PROCESSED / "account_labels.parquet"
FEATURES = DATA_PROCESSED / "account_features.parquet"
EVAL_ACCOUNTS = EVALS / "eval_accounts.csv"

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "mulecopilot")

SEED = 42
