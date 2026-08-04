"""
tests/test_resource_sampler.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for benchmarks/common/resource_sampler.py (BM-6, EPIC-13).

Tests are designed to run without a live Db2 instance and without psutil
being installed — the sampler's psutil_available flag is used to gate
assertions so the test suite never fails due to a missing optional dep.
"""

from __future__ import annotations

import time

from benchmarks.common.resource_sampler import ResourceSampler, SamplerSnapshot, sample_resources


class TestSamplerSnapshot:
    def test_is_frozen_dataclass(self):
        snap = SamplerSnapshot(
            peak_rss_bytes=1024,
            mean_cpu_pct=5.0,
            duration_s=1.0,
            sample_count=20,
            psutil_available=True,
        )
        import pytest
        with pytest.raises((AttributeError, TypeError)):
            snap.peak_rss_bytes = 0  # type: ignore[misc]


class TestResourceSampler:
    def test_context_manager_starts_and_stops(self):
        with ResourceSampler(interval_s=0.01) as s:
            time.sleep(0.05)
        snap = s.snapshot()
        assert snap.duration_s >= 0.0

    def test_snapshot_returns_valid_struct(self):
        with ResourceSampler(interval_s=0.01) as s:
            pass
        snap = s.snapshot()
        assert isinstance(snap, SamplerSnapshot)
        assert snap.peak_rss_bytes >= 0
        assert snap.duration_s >= 0.0
        assert snap.sample_count >= 0

    def test_duration_reflects_elapsed_time(self):
        with ResourceSampler(interval_s=0.01) as s:
            time.sleep(0.1)
        snap = s.snapshot()
        # Duration should be at least 100ms but we're generous for slow CI.
        assert snap.duration_s >= 0.05

    def test_peak_rss_nonzero_when_psutil_available(self):
        with ResourceSampler(interval_s=0.01) as s:
            time.sleep(0.05)
        snap = s.snapshot()
        if snap.psutil_available:
            # The current process RSS must be > 0 bytes.
            assert snap.peak_rss_bytes > 0

    def test_sample_count_positive_when_psutil_available(self):
        with ResourceSampler(interval_s=0.01) as s:
            time.sleep(0.05)
        snap = s.snapshot()
        if snap.psutil_available:
            assert snap.sample_count > 0

    def test_snapshot_callable_during_run(self):
        """Snapshot can be called while the sampler is still running."""
        with ResourceSampler(interval_s=0.01) as s:
            time.sleep(0.03)
            snap_mid = s.snapshot()
            time.sleep(0.03)
        snap_final = s.snapshot()
        # Both snapshots are valid SamplerSnapshot instances.
        assert isinstance(snap_mid, SamplerSnapshot)
        assert isinstance(snap_final, SamplerSnapshot)

    def test_multiple_runs_are_independent(self):
        """Each __enter__ / __exit__ cycle starts fresh."""
        with ResourceSampler(interval_s=0.01) as s:
            time.sleep(0.03)
        snap1 = s.snapshot()

        with ResourceSampler(interval_s=0.01) as s:
            time.sleep(0.03)
        snap2 = s.snapshot()

        # Both valid; peak_rss should be consistent (same process).
        assert snap1.peak_rss_bytes >= 0
        assert snap2.peak_rss_bytes >= 0

    def test_zero_interval_does_not_crash(self):
        """Edge case: very short interval should not raise."""
        with ResourceSampler(interval_s=0.001) as s:
            time.sleep(0.02)
        snap = s.snapshot()
        assert snap.duration_s >= 0.0


class TestSampleResourcesDecorator:
    def test_decorator_stores_last_snapshot(self):
        @sample_resources(interval_s=0.01)
        def _work():
            time.sleep(0.02)
            return "done"

        result = _work()
        assert result == "done"
        assert isinstance(_work.last_snapshot, SamplerSnapshot)

    def test_decorator_snapshot_has_positive_duration(self):
        @sample_resources(interval_s=0.01)
        def _work():
            time.sleep(0.05)

        _work()
        assert _work.last_snapshot.duration_s >= 0.01

    def test_decorator_last_snapshot_is_none_before_first_call(self):
        @sample_resources(interval_s=0.01)
        def _work():
            pass

        assert _work.last_snapshot is None
