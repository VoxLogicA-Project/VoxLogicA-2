"""Shared pytest fixtures for VoxLogicA runtime tests."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_IMPL = REPO_ROOT / "implementation" / "python"
if str(PYTHON_IMPL) not in sys.path:
    sys.path.insert(0, str(PYTHON_IMPL))


@pytest.fixture
def reduce_from_text():
    from voxlogica.parser import parse_program_content
    from voxlogica.reducer import reduce_program

    def _reduce(program_text: str):
        program = parse_program_content(program_text)
        return reduce_program(program)

    return _reduce


@pytest.fixture(params=["strict", "dask"])
def strategy_name(request):
    return request.param


@pytest.fixture
def execution_engine():
    from voxlogica.execution import ExecutionEngine

    return ExecutionEngine()


@pytest.fixture
def sample_dataset_file(tmp_path: Path) -> Path:
    dataset_path = tmp_path / "dataset.txt"
    dataset_path.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
    return dataset_path
