from __future__ import annotations

import pytest

from voxlogica.primitives.registry import PrimitiveRegistry


@pytest.mark.unit
def test_simpleitk_statistics_primitive_registered_and_executable():
    sitk = pytest.importorskip("SimpleITK")
    np = pytest.importorskip("numpy")

    registry = PrimitiveRegistry()
    registry.import_namespace("simpleitk")

    spec = registry.resolve("Statistics")
    assert spec.qualified_name == "simpleitk.Statistics"

    kernel = registry.load_kernel("Statistics")
    image = sitk.GetImageFromArray(np.asarray([[0, 10], [20, 30]], dtype=np.float32))
    result = kernel(image)

    assert isinstance(result, tuple)
    assert len(result) == 7
    assert result[0] == pytest.approx(0.0)
    assert result[1] == pytest.approx(30.0)
    assert result[2] == pytest.approx(15.0)
    assert result[6] == 4

