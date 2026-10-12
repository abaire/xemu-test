"""Test harness for test-xbe."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from xemutest import Environment, XemuTestBase

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG: str = """log_path = c:\\results\\results.txt
shutdown = true
"""

TEST_HEADER_RE = re.compile(r"^Test:\s*(.+)$")
ASSERTION_RE = re.compile(r"^\s*\[(PASS|FAIL)\]\s*(.+)$")
NOTE_RE = re.compile(r"^\s*\[NOTE\].*$")
SUBSECTION_RE = re.compile(r"^\s{1,4}(\S.*)$")

#    1 FPU Basic Arithmetic             : PASS
#    2 X87 fld/fstp Precision Control   : FAIL
SUMMARY_LINE_RE = re.compile(r"^\s*\d+\s+.+:\s+(?:PASS|FAIL)\s*$")


class NxdkLowLevelNV2ATestsBase(XemuTestBase):
    """Base class for tests using nxdk_low_level_nv2a_tests."""

    def __init__(
        self,
        test_env: Environment,
        results_path: Path,
        test_data_path: Path,
        iso_filename: Path,
        *args,
        **kwargs,
    ):
        super().__init__(test_env, results_path, *args, **kwargs)
        self.xemu_manager.iso_path = test_data_path / iso_filename
        self.xbox_results_path = "results"

    def analyze_results(self):
        results = self._fetch_results_as_json()

        with open(self.results_path / "results.json", "w") as outfile:
            json.dump(results, outfile, indent=2)
        logger.debug("%s", json.dumps(results, indent=2))

        assert results["status"] == "PASS"

    def _fetch_results_as_json(self) -> dict[str, Any]:
        results_string = self._fetch_results()
        assert results_string

        results = self._parse_test_log(results_string)
        assert results

        return results

    def _fetch_results(self) -> str | None:
        results_file = self.results_path / "results.txt"
        if not results_file.exists():
            return None

        return results_file.read_text().strip()

    def _parse_test_log(self, log_text: str) -> list[dict[str, Any]]:
        suites: list[dict[str, Any]] = []
        current_suite: dict[str, Any] | None = None
        current_section: str | None = None

        for line in log_text.splitlines():
            line = line.rstrip()  # noqa: PLW2901 `for` loop variable overwritten by assignment target
            stripped = line.strip()

            if not stripped or stripped.startswith("==="):
                continue

            if SUMMARY_LINE_RE.match(line) or NOTE_RE.match(line):
                continue

            header_match = TEST_HEADER_RE.match(stripped)
            if header_match:
                current_suite = {
                    "name": header_match.group(1).strip(),
                    "cases": [],
                }
                suites.append(current_suite)
                current_section = None
                continue

            if current_suite is None:
                continue

            assert_match = ASSERTION_RE.match(line)
            if assert_match:
                status, description = assert_match.groups()
                case_result = {
                    "status": status,
                    "description": description.strip(),
                }
                if current_section:
                    case_result["subsection"] = current_section
                current_suite["cases"].append(case_result)
                continue

            subsec_match = SUBSECTION_RE.match(line)
            if subsec_match:
                current_section = subsec_match.group(1).strip()

        for suite in suites:
            cases = suite["cases"]
            suite["status"] = "FAIL" if any(c["status"] == "FAIL" for c in cases) else "PASS" if cases else "UNKNOWN"
            suite["passed"] = sum(1 for c in cases if c["status"] == "PASS")
            suite["failed"] = sum(1 for c in cases if c["status"] == "FAIL")

        return {
            "suites": suites,
            "status": "FAIL" if any(s["status"] == "FAIL" for s in suites) else "PASS" if suites else "UNKNOWN",
            "passed": sum(s["passed"] for s in suites),
            "failed": sum(s["failed"] for s in suites),
        }

    def _prepare_hdd(self):
        super()._prepare_hdd()

        logger.debug("Writing C:/test_suite_config.ini")
        fs_c = self.hdd_manager.get_filesystem("c")
        fs_c.write("/test_suite_config.ini", _CONFIG.encode())
        del fs_c
