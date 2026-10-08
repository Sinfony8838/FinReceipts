"""Dependency metadata and timezone fallback checks, runnable without pytest."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DependencyContractTests(unittest.TestCase):
    def test_cninfo_timezone_data_is_an_unconditional_runtime_dependency(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        requirements = metadata["project"]["dependencies"]
        tzdata = [value for value in requirements if re.match(r"^tzdata(?:\b|[<>=!~])", value)]
        self.assertEqual(len(tzdata), 1)
        # Minimal Linux containers can also lack system zoneinfo, not only Windows.
        self.assertNotIn(";", tzdata[0])

    def test_api_test_dependency_is_declared_in_dev_extra(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        dev = metadata["project"]["optional-dependencies"]["dev"]
        self.assertTrue(any(re.match(r"^fastapi(?:\b|[<>=!~])", value) for value in dev))

    def test_cninfo_dates_without_system_timezone_database(self):
        # A fresh process avoids cached ZoneInfo objects and proves tzdata fallback.
        # No client is constructed, and this test performs no network operations.
        source = """
import sys
from datetime import date
from zoneinfo import TZPATH, ZoneInfo

sys.path.insert(0, sys.argv[1])
from finreceipts.tools.cninfo import announcement_date

assert TZPATH == (), TZPATH
assert ZoneInfo("Asia/Shanghai").key == "Asia/Shanghai"
assert announcement_date(1743609599999) == date(2025, 4, 2)
assert announcement_date(1743609600000) == date(2025, 4, 3)
"""
        result = subprocess.run(
            [sys.executable, "-c", source, str(ROOT / "src")],
            env={"PYTHONTZPATH": "", "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
