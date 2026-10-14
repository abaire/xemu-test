import argparse
import importlib
import inspect
import json
import logging
import sys
from pathlib import Path

from xemutest import Environment, TestBase, ci
from xemutest.test_base import TestResult, TestStatus

log = logging.getLogger(__name__)

SUMMARY_FILENAME = "summary.json"


def discover_tests(
    tests_dir: Path, only: set[str] | None = None
) -> list[tuple[str, type[TestBase]]]:
    """Discover test classes in tests_dir, sorted by name for stable partitioning."""
    tests: list[tuple[str, type[TestBase]]] = []
    if not tests_dir.is_dir():
        return tests

    if str(tests_dir) not in sys.path:
        sys.path.append(str(tests_dir))
    for path in sorted(tests_dir.iterdir()):
        if not path.name.startswith("test_") or path.suffix != ".py":
            continue

        module = importlib.import_module(path.stem)
        for test_name, test_class in inspect.getmembers(module, inspect.isclass):
            if (
                test_name.startswith("Test")
                and issubclass(test_class, TestBase)
                and test_class is not TestBase
            ):
                if only and test_name not in only:
                    continue
                tests.append((test_name, test_class))

    tests.sort(key=lambda t: t[0])
    return tests


def partition_tests[T](tests: list[T], num_partitions: int) -> list[list[T]]:
    """Deterministically distributes tests across num_partitions (round-robin)."""
    if num_partitions <= 0:
        msg = f"num_partitions must be positive, got {num_partitions}"
        raise ValueError(msg)
    partitions: list[list[T]] = [[] for _ in range(num_partitions)]
    for idx, test in enumerate(tests):
        partitions[idx % num_partitions].append(test)
    return partitions


def format_status(r: TestResult) -> str:
    match r.status:
        case TestStatus.PASSED:
            return "✅ Passed"
        case TestStatus.FAILED:
            return "❌ Failed"
        case TestStatus.UNVERIFIED:
            return "⚠️ Unverified"
        case TestStatus.RUNNING:
            return "🔄 Running"
        case TestStatus.FAILURE_SUPPRESSED:
            return "⚠️ Failure suppressed by failure filter"
        case TestStatus.UNEXPECTED_FILTER_MISMATCH:
            return "❌ Test state does not match failure filter expectation"


def collect_rows(
    test_result: TestResult,
    parent_path: str = "",
    *,
    omit_innermost_passed_subtests: bool = False,
) -> list[list[str]]:
    """Recursively collect rows for a test and its subtests."""
    full_name = (
        f"{parent_path}::{test_result.name}" if parent_path else test_result.name
    )

    if (
        parent_path
        and not test_result.subtests
        and test_result.status == TestStatus.PASSED
        and omit_innermost_passed_subtests
    ):
        return []

    rows = [
        [
            f"[{full_name}]({test_result.location_url})"
            if test_result.location_url
            else full_name,
            format_status(test_result),
            test_result.duration,
            test_result.message,
        ]
    ]
    for subtest in test_result.subtests:
        rows.extend(
            collect_rows(
                subtest,
                full_name,
                omit_innermost_passed_subtests=omit_innermost_passed_subtests,
            )
        )
    return rows


def write_job_summary(
    test_results: dict[str, TestResult],
    title: str = "xemu Test Results",
    *,
    omit_innermost_passed_subtests: bool = False,
):
    """Write a job summary for GitHub Actions."""
    if not ci.is_github_actions():
        return

    summary = ci.JobSummary()
    summary.add_heading(title)

    rows: list[list[str]] = []
    for test_result in test_results.values():
        rows.extend(
            collect_rows(
                test_result,
                omit_innermost_passed_subtests=omit_innermost_passed_subtests,
            )
        )

    summary.add_table(headers=["Test", "Status", "Duration", "Details"], rows=rows)
    summary.write()


def write_results_summary(test_results: dict[str, TestResult], output_file: Path):
    """Serializes test_results to a JSON file so that they may be aggregated."""
    output_file.write_text(
        json.dumps({k: v.to_dict() for k, v in test_results.items()}, indent=2),
        encoding="utf-8",
    )


def aggregate_results(
    results_dirs: list[Path],
    output_dir: Path | None = None,
    *,
    omit_innermost_passed_subtests: bool = False,
) -> bool:
    """Merges the JSON summaries from multiple partitioned runs.

    Writes a consolidated summary to output_dir (if given) and to the GitHub
    Actions job summary. Returns False if any test failed or any summary could
    not be read.
    """
    aggregated: dict[str, TestResult] = {}
    all_ok = True

    for results_dir in results_dirs:
        summary_file = results_dir / SUMMARY_FILENAME
        if not summary_file.is_file():
            log.error("Summary file not found: %s", summary_file)
            all_ok = False
            continue

        try:
            data = json.loads(summary_file.read_text(encoding="utf-8"))
            for name, result_dict in data.items():
                result = TestResult.from_dict(result_dict)
                aggregated[name] = result
                if not result.ok:
                    all_ok = False
        except Exception:
            log.exception("Failed to read summary file: %s", summary_file)
            all_ok = False

    aggregated = dict(sorted(aggregated.items()))

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        write_results_summary(aggregated, output_dir / SUMMARY_FILENAME)

    write_job_summary(
        aggregated,
        title="Consolidated xemu Test Results",
        omit_innermost_passed_subtests=omit_innermost_passed_subtests,
    )
    return all_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xemu", nargs="?", help="Path to the xemu binary")
    ap.add_argument("private", nargs="?", help="Path to private data files")
    ap.add_argument(
        "results", nargs="?", help="Path to directory where results should go"
    )
    ap.add_argument("--data", help="Path to test data directory")
    ap.add_argument("--ffmpeg", help="Path to the ffmpeg binary")

    ap.add_argument(
        "--no-mcpx",
        action="store_true",
        help="Suppress the requirement for an MCPX boot ROM (e.g. when using Roswell BIOS)",
    )
    ap.add_argument(
        "--sysram", type=int, default=64, choices=[64, 128], help="Guest RAM size"
    )
    ap.add_argument(
        "--no-hard-fpu",
        action="store_true",
        help="Disable xemu hardware-accelerated FPU",
    )
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        help="Specify the specific tests to run (may be specified multiple times)",
    )
    ap.add_argument(
        "--failure-filter",
        help=(
            "Path to JSON test failure filter. Failures that match entries in this file will be listed as a warning "
            "rather than a failure. Tests that pass unexpectedly will be treated as a failure."
        ),
    )
    ap.add_argument(
        "--ci-summary-omit-innermost-passed-subtests",
        action="store_true",
        help="Omit 'Passed' results from the innermost level of subtests from the GitHub summary table.",
    )
    ap.add_argument(
        "-v", "--verbose", action="store_true", help="Print verbose logging information"
    )

    # Partitioning/discovery options
    ap.add_argument(
        "--count",
        action="store_true",
        help="Print the total number of discovered test suites and exit",
    )
    ap.add_argument(
        "--list-partitions",
        action="store_true",
        help="Print the test suites assigned to each partition and exit",
    )
    ap.add_argument(
        "--num-partitions",
        type=int,
        default=1,
        help="Total number of partitions to split the test suites into",
    )
    ap.add_argument(
        "--partition-index",
        type=int,
        default=0,
        help="0-based index of the partition to execute",
    )
    ap.add_argument(
        "--aggregate-results",
        nargs="+",
        metavar="DIR",
        help=(
            "Merge the results of partitioned runs from the given directories, write a "
            "consolidated job summary, and exit"
        ),
    )
    ap.add_argument(
        "--aggregate-output",
        metavar="DIR",
        help="Directory into which the merged summary.json from --aggregate-results is written",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    logging.getLogger("pyfatx").setLevel(logging.WARNING)

    # Add GitHub Actions annotation handler for warnings/errors
    if ci.is_github_actions():
        logging.getLogger().addHandler(ci.GitHubActionsHandler())

    if args.aggregate_results:
        results_dirs = [Path(d).expanduser().resolve() for d in args.aggregate_results]
        output_dir = (
            Path(args.aggregate_output).expanduser().resolve()
            if args.aggregate_output
            else None
        )
        ok = aggregate_results(
            results_dirs,
            output_dir,
            omit_innermost_passed_subtests=args.ci_summary_omit_innermost_passed_subtests,
        )
        sys.exit(0 if ok else 1)

    if args.num_partitions <= 0:
        ap.error(f"--num-partitions must be positive, got {args.num_partitions}")
    if not 0 <= args.partition_index < args.num_partitions:
        ap.error(
            f"--partition-index must be between 0 and {args.num_partitions - 1}, "
            f"got {args.partition_index}"
        )

    this_dir = Path(__file__).resolve().parent
    only_filter = set(args.only) if args.only else None
    all_tests = discover_tests(this_dir / "tests", only_filter)

    if args.count:
        print(len(all_tests))
        sys.exit(0)

    partitions = partition_tests(all_tests, args.num_partitions)

    if args.list_partitions:
        for i, partition in enumerate(partitions):
            names = [name for name, _ in partition]
            print(
                f"Partition {i} ({len(names)} tests): "
                f"{', '.join(names) if names else '(empty)'}"
            )
        sys.exit(0)

    if not (args.xemu and args.private and args.results):
        ap.error(
            "the following arguments are required: xemu, private, results "
            "(unless using --count, --list-partitions, or --aggregate-results)"
        )

    tests = partitions[args.partition_index]
    if args.num_partitions > 1:
        log.info(
            "Partition %d/%d assigned %d of %d tests: %s",
            args.partition_index + 1,
            args.num_partitions,
            len(tests),
            len(all_tests),
            [name for name, _ in tests],
        )

    xemu_path = Path(args.xemu).expanduser().resolve()
    private_path = Path(args.private).expanduser().resolve()
    test_data_root = (
        Path(args.data).expanduser().resolve() if args.data else (this_dir / "data")
    )
    ffmpeg_path = Path(args.ffmpeg).expanduser().resolve() if args.ffmpeg else None

    # Validate required paths
    errors = []
    if not xemu_path.is_file():
        errors.append(f"xemu binary not found: {xemu_path}")
    if not private_path.is_dir():
        errors.append(f"Private data directory not found: {private_path}")
    if not test_data_root.is_dir():
        errors.append(f"Test data directory not found: {test_data_root}")
    if ffmpeg_path and not ffmpeg_path.is_file():
        errors.append(f"ffmpeg binary not found: {ffmpeg_path}")
    if errors:
        for error in errors:
            log.error(error)
        sys.exit(1)

    result = True
    results_root = Path(args.results).expanduser().resolve()
    results_root.mkdir(parents=True, exist_ok=True)

    test_env = Environment(
        private_path,
        xemu_path,
        ffmpeg_path,
        require_mcpx=not args.no_mcpx,
        sysram=args.sysram,
        hard_fpu=not args.no_hard_fpu,
        failure_filter_path=args.failure_filter,
    )

    test_results_summary: dict[str, TestResult] = {}

    for i, (test_name, test_cls) in enumerate(tests):
        test_results = results_root / test_name
        test_data = test_data_root / test_name
        with ci.log_group(f"Test {i}: {test_name}"):
            try:
                log.info("Test %d - %s: Starting", i, test_name)
                test = test_cls(test_env, test_results, test_data)
                test_result = test.run()
                log.info("Test %d - %s: Finished", i, test_name)
                test_results_summary[test_name] = test_result
                if not test_result.ok:
                    result = False
            except BaseException:
                log.exception("Test %d - %s: Failed", i, test_name)
                test_results_summary[test_name] = TestResult(
                    name=test_name, status=TestStatus.FAILED
                )
                result = False

    # Persist results so that partitioned runs can be aggregated later.
    summary_file = results_root / SUMMARY_FILENAME
    try:
        write_results_summary(test_results_summary, summary_file)
    except Exception:
        log.exception("Failed to write summary file: %s", summary_file)
        result = False

    write_job_summary(
        test_results_summary,
        title=(
            "xemu Test Results "
            f"(Partition {args.partition_index + 1}/{args.num_partitions})"
            if args.num_partitions > 1
            else "xemu Test Results"
        ),
        omit_innermost_passed_subtests=args.ci_summary_omit_innermost_passed_subtests,
    )

    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
