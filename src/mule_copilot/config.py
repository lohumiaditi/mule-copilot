import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_PROCESSED = ROOT / "data" / "processed"
DOCS_DIR = ROOT / "data" / "raw" / "docs"
CHROMA_DIR = ROOT / "chroma"
EVALS = ROOT / "evals"

TRANSACTIONS = DATA_PROCESSED / "transactions.parquet"
LABELS = DATA_PROCESSED / "account_labels.parquet"
FEATURES = DATA_PROCESSED / "account_features.parquet"
CLAUSES = DATA_PROCESSED / "clauses.jsonl"
MIS_DB = DATA_PROCESSED / "mis.sqlite"
CHECKPOINTS_DB = DATA_PROCESSED / "checkpoints.sqlite"
EXPLANATIONS = ROOT / "evals" / "explanations.csv"

# Synthetic bank details for drafted documents. Not a real bank.
BANK_NAME = "Example Bank Ltd (synthetic)"
NODAL_OFFICER = "Nodal Officer, Mule Investigations Desk, Example Bank Ltd, nodal.officer@example.com"
EVAL_ACCOUNTS = EVALS / "eval_accounts.csv"
NCRP_REPORTED = EVALS / "ncrp_reported.csv"

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "mulecopilot")

GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

SEED = 42
