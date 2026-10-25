"""Separate evaluation purse: capped at CNY 10, leaving at most CNY 10 for business runs.

This is a single-process experiment journal, not the business coordinator. It
never resets on restart, and an interrupted attempt keeps its full reservation.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path


class EvalBudget:
    LIMIT = 10_000_000

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "budget.jsonl"
        self.lock = directory / "budget.lock"
        self.lock_fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(self.lock_fd, str(os.getpid()).encode())
        self.entries = (
            [json.loads(s) for s in self.path.read_text().splitlines()]
            if self.path.exists()
            else []
        )

    def append(self, row):
        with self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps({**row, "time": time.time()}) + "\n")
            out.flush()
            os.fsync(out.fileno())
        self.entries.append(row)

    def total(self):
        amounts = {}
        for row in self.entries:
            amounts[row["id"]] = row["micros"]
        return sum(amounts.values())

    def reserve(self, amount, case, provider):
        if amount <= 0 or self.total() + amount > self.LIMIT:
            raise RuntimeError("evaluation_budget_exhausted")
        key = str(uuid.uuid4())
        self.append(
            {"id": key, "state": "RESERVED", "micros": amount, "case": case, "provider": provider}
        )
        return key

    def settle(self, key, amount, usage):
        prior = next(row for row in reversed(self.entries) if row["id"] == key)
        if prior["state"] != "RESERVED" or not 0 <= amount <= prior["micros"]:
            raise RuntimeError("invalid_evaluation_settlement")
        self.append({"id": key, "state": "SETTLED", "micros": amount, "usage": usage})

    def close(self):
        os.close(self.lock_fd)
        self.lock.unlink()
