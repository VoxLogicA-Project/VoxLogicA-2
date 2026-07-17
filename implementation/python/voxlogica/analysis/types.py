from typing import Callable
from abc import ABC
from dataclasses import dataclass


class VoxType(ABC):
    pass


@dataclass(frozen=True)
class VoxNumber(VoxType):
    pass


@dataclass(frozen=True)
class VoxInt(VoxNumber):
    pass


@dataclass(frozen=True)
class VoxFloat(VoxNumber):
    pass


@dataclass(frozen=True)
class VoxBool(VoxType):
    pass


@dataclass(frozen=True)
class VoxString(VoxType):
    pass


@dataclass(frozen=True)
class VoxImage(VoxType):
    pass


@dataclass(frozen=True)
class VoxSequence(VoxType):
    element_type: VoxType


@dataclass(frozen=True)
class VoxClosure(VoxType):
    argument_type: VoxType
    return_type: VoxType


TypeRule = Callable[[list[VoxType]], VoxType]