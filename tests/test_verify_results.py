"""Tests for interpreting caravel_cocotb run directories in `cf verify`."""

from chipfoundry_cli.main import _cocotb_run_results


def _test_dir(run_dir, name, marker=None):
    d = run_dir / name
    d.mkdir(parents=True)
    if marker:
        (d / marker).touch()


def test_all_passed(tmp_path):
    _test_dir(tmp_path, "RTL-compilation")
    _test_dir(tmp_path, "RTL-counter", "passed")
    _test_dir(tmp_path, "RTL-timer", "passed")
    assert _cocotb_run_results(tmp_path) == (["RTL-counter", "RTL-timer"], [])


def test_failed_test_is_reported(tmp_path):
    _test_dir(tmp_path, "RTL-counter", "passed")
    _test_dir(tmp_path, "RTL-timer", "failed")
    assert _cocotb_run_results(tmp_path) == (["RTL-counter"], ["RTL-timer"])


def test_missing_run_dir(tmp_path):
    assert _cocotb_run_results(tmp_path / "nope") == ([], [])
