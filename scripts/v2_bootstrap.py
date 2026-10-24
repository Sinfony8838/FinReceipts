"""Create isolated Compose credentials; never overwrite an existing environment."""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
destination = root / ".env.v2"
values = {
    "DATABASE_PASSWORD": secrets.token_urlsafe(32),
    "RABBITMQ_PASSWORD": secrets.token_urlsafe(32),
    "WORKER_SECRET": secrets.token_urlsafe(48),
    "BOOTSTRAP_PASSWORD": secrets.token_urlsafe(24),
    "MODEL_PROVIDER": "fake",
    "MODEL_ID": "MiniMax-M3.1-Flash",
    "MODEL_BUDGET_MICROS": "10000000",
}
with destination.open("x", encoding="utf-8") as output:
    output.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
print(f"Created {destination}; credentials were not printed.")
print("Accounts: alpha.admin, alpha.analyst, alpha.reviewer; beta equivalents.")
print("Read BOOTSTRAP_PASSWORD locally from .env.v2. It is shared only for initial local setup.")
