"""Unit tests for the test partitioning and result aggregation helpers."""

import json
from pathlib import Path

import pytest

from xemutest import test_base
from xemutest.__main__ import aggregate_results, discover_tests, partition_tests

# Aliased so that pytest does not try to collect them as test classes.
Result = test_base.TestResult
Status = test_base.TestStatus

_TESTS_DIR = Path(__file__).resolve().parent.parent / "xemutest" / "tests"


def test_discover_tests():
    tests = discover_tests(_TESTS_DIR)
    names = [name for name, _ in tests]
    assert "TestXBE" in names
    assert "TestNxdkLowLevelCPUTests__FpuTestXiso" in names
    assert names == sorted(names)


def test_discover_tests_only_filter():
    tests = discover_tests(_TESTS_DIR, {"TestXBE"})
    assert [name for name, _ in tests] == ["TestXBE"]


def test_partition_tests():
    items = ["A", "B", "C", "D", "E"]
    assert partition_tests(items, 2) == [["A", "C", "E"], ["B", "D"]]
    assert partition_tests(items, 1) == [items]

    parts = partition_tests(items, 7)
    assert parts == [["A"], ["B"], ["C"], ["D"], ["E"], [], []]

    with pytest.raises(ValueError):
        partition_tests(items, 0)


def test_test_result_round_trip():
    result = Result(
        name="root",
        status=Status.FAILURE_SUPPRESSED,
        message="error",
        duration="100ms",
        location_url="https://example.com",
        subtests=[Result(name="sub", status=Status.PASSED, duration="12ms")],
    )
    restored = Result.from_dict(json.loads(json.dumps(result.to_dict())))
    assert restored == result


def test_aggregate_results(tmp_path: Path):
    shard0 = tmp_path / "shard0"
    shard1 = tmp_path / "shard1"
    out_dir = tmp_path / "out"
    shard0.mkdir()
    shard1.mkdir()

    passed = Result(name="Test1", status=Status.PASSED)
    failed = Result(name="Test2", status=Status.FAILED, message="failure")
    (shard0 / "summary.json").write_text(json.dumps({"Test1": passed.to_dict()}))
    (shard1 / "summary.json").write_text(json.dumps({"Test2": failed.to_dict()}))

    assert aggregate_results([shard0, shard1], out_dir) is False

    merged = json.loads((out_dir / "summary.json").read_text())
    assert merged["Test1"]["status"] == "passed"
    assert merged["Test2"]["status"] == "failed"


def test_aggregate_results_suppressed_failures_are_ok(tmp_path: Path):
    shard = tmp_path / "shard"
    shard.mkdir()
    suppressed = Result(name="Test1", status=Status.FAILURE_SUPPRESSED)
    (shard / "summary.json").write_text(json.dumps({"Test1": suppressed.to_dict()}))

    assert aggregate_results([shard]) is True


def test_aggregate_results_missing_summary_fails(tmp_path: Path):
    assert aggregate_results([tmp_path / "missing"]) is False
