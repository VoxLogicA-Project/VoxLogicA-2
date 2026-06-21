from __future__ import annotations

import numpy as np
import pytest

from voxlogica.storage import MaterializationStore


@pytest.mark.unit
def test_image_like_value_is_memoized_in_ram_without_backend() -> None:
    """An image-like result must stay in RAM so a shared node is computed once.

    Image-like values (here an ndarray) used to be replaced by a node-id
    placeholder in the in-memory record, so ``get`` returned ``None`` whenever
    no backend was present (e.g. --no-cache) and the node was recomputed on
    every reuse. The live value must now be returned directly.
    """
    store = MaterializationStore(backend=None, read_through=False, write_through=False)
    array = np.arange(12, dtype=np.float32).reshape(3, 4)

    store.put("node-image", "expr", [], array, metadata={"source": "runtime"})

    cached = store.get("node-image")
    assert cached is not None
    assert np.array_equal(cached, array)


@pytest.mark.unit
def test_scalar_value_round_trips_in_ram_without_backend() -> None:
    store = MaterializationStore(backend=None, read_through=False, write_through=False)
    store.put("node-scalar", "expr", [], 42.0, metadata={"source": "runtime"})
    assert store.get("node-scalar") == 42.0
