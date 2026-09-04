"""percentiles()'s parallel-sort path — bit-identical vs a plain reference.

percentiles() was measured to spend ~93% of its time in one np.argsort call
(see doc/dev/dynamic-scheduler/frontier-scheduler.md "percentiles decomposed").
Sorting parallelizes with cores (unlike elementwise fusion, which doesn't —
see the same doc). This locks in that the parallel-sort/merge path (used once
the population clears NumbaFusionBackend-independent
``kernels._PARALLEL_SORT_MIN_POPULATION``) produces IDENTICAL results to the
plain single-thread path, including tie-heavy and edge-case populations —
there was no test for this kernel before.
"""

from __future__ import annotations

import numpy as np
import pytest

from voxlogica.primitives.vox1 import kernels


def _reference(img_values: np.ndarray, mask_values: np.ndarray, correction: float) -> np.ndarray:
    """Plain numpy percentile-rank, independent of the kernel under test."""
    result_values = np.full(img_values.shape, -1.0, dtype=np.float32)
    population = np.flatnonzero(mask_values > 0)
    if population.size == 0:
        return result_values
    population_values = img_values[population]
    sorted_order = np.argsort(population_values, kind="mergesort")
    sorted_indices = population[sorted_order]
    sorted_values = population_values[sorted_order]
    vol = float(population.size)
    curvol = 0
    group_start = 0
    while group_start < sorted_values.size:
        group_end = group_start + 1
        while group_end < sorted_values.size and sorted_values[group_end] == sorted_values[group_start]:
            group_end += 1
        group_size = group_end - group_start
        value = (float(curvol) + (float(correction) * float(group_size))) / vol
        result_values[sorted_indices[group_start:group_end]] = np.float32(value)
        curvol += float(group_size)
        group_start = group_end
    return result_values


def _percentiles_via_kernel(img_values: np.ndarray, mask_values: np.ndarray, correction: float) -> np.ndarray:
    """Drives the exact same extract/sort/group pipeline percentiles() uses."""
    population, population_values = kernels._extract_population(img_values, mask_values)
    if population.shape[0] >= kernels._PARALLEL_SORT_MIN_POPULATION and kernels._PARALLEL_SORT_CHUNKS > 1:
        sorted_values, sorted_indices = kernels._parallel_sorted_population(
            population, population_values, kernels._PARALLEL_SORT_CHUNKS)
        took_parallel = True
    else:
        order = np.argsort(population_values)
        sorted_values, sorted_indices = population_values[order], population[order]
        took_parallel = False
    result = kernels._group_and_write(sorted_values, sorted_indices, img_values.shape[0], float(correction))
    return result, took_parallel


@pytest.mark.unit
@pytest.mark.parametrize(
    "name,n,correction,heavy_ties,all_mask_off",
    [
        ("tiny_few_ties", 100, 0.5, False, False),
        ("tiny_all_mask_off", 50, 0.0, False, True),
        ("small_heavy_ties", 10_000, 0.5, True, False),
        ("large_forces_parallel", 500_000, 0.5, False, False),
        ("large_heavy_ties_parallel", 500_000, 0.0, True, False),
        ("large_odd_remainder_parallel", 700_003, 0.3, False, False),
        ("exactly_at_threshold_minus_one", kernels._PARALLEL_SORT_MIN_POPULATION, 0.5, False, False),
    ],
)
def test_percentiles_kernel_matches_reference(name, n, correction, heavy_ties, all_mask_off) -> None:
    rng = np.random.default_rng(hash(name) & 0xFFFFFFFF)
    if heavy_ties:
        img_values = rng.integers(0, 50, size=n).astype(np.float32)
    else:
        img_values = rng.uniform(0, 1000, size=n).astype(np.float32)
    if all_mask_off:
        mask_values = np.zeros(n, dtype=np.uint8)
    else:
        mask_values = (rng.uniform(0, 1, size=n) > 0.1).astype(np.uint8)

    expected = _reference(img_values, mask_values, correction)
    got, _ = _percentiles_via_kernel(img_values, mask_values, correction)
    assert np.array_equal(expected, got), f"{name}: parallel-sort kernel diverged from reference"


@pytest.mark.unit
def test_large_population_actually_takes_the_parallel_path() -> None:
    """Guards against the test above silently degrading to the serial path
    if the threshold constant ever changes — this must exercise parallel."""
    rng = np.random.default_rng(0)
    n = kernels._PARALLEL_SORT_MIN_POPULATION * 3
    img_values = rng.uniform(0, 1000, size=n).astype(np.float32)
    mask_values = np.ones(n, dtype=np.uint8)
    _, took_parallel = _percentiles_via_kernel(img_values, mask_values, 0.5)
    assert took_parallel


@pytest.mark.unit
def test_merge_sorted_pairs_matches_plain_sort_of_the_union() -> None:
    rng = np.random.default_rng(7)
    for n1, n2 in [(0, 5), (5, 0), (1, 1), (3, 7), (1000, 1), (5000, 4999)]:
        v1 = np.sort(rng.uniform(0, 100, size=n1).astype(np.float32))
        v2 = np.sort(rng.uniform(0, 100, size=n2).astype(np.float32))
        idx1 = rng.integers(0, 10_000, size=n1).astype(np.int64)
        idx2 = rng.integers(0, 10_000, size=n2).astype(np.int64)
        merged_values, merged_idx = kernels._merge_sorted_pairs(v1, idx1, v2, idx2)
        assert np.array_equal(merged_values, np.sort(np.concatenate([v1, v2])))
        assert set(merged_idx.tolist()) == set(idx1.tolist()) | set(idx2.tolist())
