from __future__ import annotations

from pathlib import Path

import dask.bag as db
import pytest

from voxlogica.execution_strategy.results import SequenceValue
from voxlogica.primitives.default import (
    addition,
    dask_map,
    division,
    for_loop,
    index,
    load,
    map as map_primitive,
    multiplication,
    print_primitive,
    range as range_primitive,
    subtraction,
)
import voxlogica.primitives.default as default_ns


@pytest.mark.unit
def test_arithmetic_primitives():
    assert addition.execute(2, 3) == 5
    assert subtraction.execute(8, 3) == 5
    assert multiplication.execute(2, 4) == 8
    assert division.execute(8, 2) == 4
    with pytest.raises(ValueError):
        division.execute(1, 0)


@pytest.mark.unit
def test_sequence_arithmetic_overloads():
    seq_add = addition.execute([1, 2, 3], 10)
    assert isinstance(seq_add, SequenceValue)
    assert list(seq_add.iter_values()) == [11, 12, 13]

    seq_mul = multiplication.execute(2, [1, 2, 3])
    assert isinstance(seq_mul, SequenceValue)
    assert list(seq_mul.iter_values()) == [2, 4, 6]

    pairwise = subtraction.execute([10, 20, 30], [1, 2, 3])
    assert isinstance(pairwise, SequenceValue)
    assert list(pairwise.iter_values()) == [9, 18, 27]

    with pytest.raises(ValueError):
        list(addition.execute([1, 2], [1, 2, 3]).iter_values())

    with pytest.raises(ValueError):
        list(division.execute([1, 2], 0).iter_values())


@pytest.mark.unit
def test_dask_arithmetic_overloads():
    bag = db.from_sequence([1, 2, 3], npartitions=2)
    assert addition.execute(bag, 5).compute() == [6, 7, 8]
    assert multiplication.execute(3, bag).compute() == [3, 6, 9]
    assert subtraction.execute(bag, bag).compute() == [0, 0, 0]


@pytest.mark.unit
def test_index_primitive():
    assert index.execute(**{"0": ["a", "b"], "1": 1}) == "b"
    assert index.execute(**{"0": ["a", "b"], "1": 1.0}) == "b"
    assert index.execute(**{"0": ["a", "b"], "1": "1"}) == "b"
    with pytest.raises(ValueError):
        index.execute(**{"0": ["a"], "1": "x"})


@pytest.mark.unit
def test_range_primitive():
    assert range_primitive.execute(**{"0": 4}).compute() == [0, 1, 2, 3]
    assert range_primitive.execute(**{"0": 2, "1": 5}).compute() == [2, 3, 4]
    assert range_primitive.execute(**{"0": 5, "1": 2}).compute() == []
    with pytest.raises(ValueError):
        range_primitive.execute(**{"0": 1.2})


@pytest.mark.unit
def test_load_primitive(tmp_path: Path):
    assert load.execute(**{"0": (1, 2, 3)}) == [1, 2, 3]

    json_file = tmp_path / "x.json"
    json_file.write_text("[1,2]", encoding="utf-8")
    assert load.execute(**{"0": str(json_file)}) == [1, 2]

    txt_file = tmp_path / "x.txt"
    txt_file.write_text("a\nb\n", encoding="utf-8")
    assert load.execute(**{"0": str(txt_file)}) == ["a", "b"]

    bin_file = tmp_path / "x.bin"
    bin_file.write_bytes(b"\x01\x02")
    assert load.execute(**{"0": str(bin_file)}) == b"\x01\x02"

    with pytest.raises(ValueError):
        load.execute(**{"0": str(tmp_path / "missing.txt")})


@pytest.mark.unit
def test_map_and_for_loop_primitives():
    class Closure:
        def apply(self, value):
            return value + 10

    assert map_primitive.execute(**{"0": [1, 2], "closure": lambda x: x * 2}) == [2, 4]
    assert map_primitive.execute(**{"0": [1, 2], "1": Closure()}) == [11, 12]
    assert for_loop.execute(**{"0": [1, 2], "closure": lambda x: x + 1}) == [2, 3]

    class Computable:
        def compute(self):
            return [3, 4]

    assert map_primitive.execute(**{"0": Computable(), "closure": lambda x: x}) == [3, 4]
    assert for_loop.execute(**{"0": Computable(), "1": lambda x: x * 2}) == [6, 8]

    with pytest.raises(ValueError):
        map_primitive.execute(**{"0": [1, 2]})
    with pytest.raises(ValueError):
        for_loop.execute(**{"0": [1, 2]})


@pytest.mark.unit
def test_dask_map_and_print_primitive(capsys: pytest.CaptureFixture[str]):
    class DaskClosure:
        variable = "x"

        def __call__(self, value):
            return value + 1

    bag = db.from_sequence([1, 2, 3], npartitions=2)
    mapped = dask_map.execute(**{"0": bag, "closure": DaskClosure()})
    assert mapped.compute() == [2, 3, 4]

    with pytest.raises(ValueError):
        dask_map.execute(**{"0": [1, 2, 3], "closure": DaskClosure()})

    rendered = print_primitive.execute(**{"0": '"label"', "1": 42})
    assert rendered == "label=42"
    assert "label=42" in capsys.readouterr().out


@pytest.mark.unit
def test_default_namespace_list_primitives():
    primitives = default_ns.list_primitives()
    assert "addition" in primitives
    assert "range" in primitives
    assert isinstance(primitives["addition"], str)
