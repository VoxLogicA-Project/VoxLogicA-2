"""Named execution strategies.

One table maps a NAME to a runtime, so that `voxlogica run --engine <name>`,
the `strategy=` argument of every `ExecutionEngine` method, and the error
message that lists the choices all agree on what the names are. Before this
existed the choice was a boolean (`use_engine`) and every method that took a
`strategy` argument began with `del strategy` -- a name was accepted, silently
discarded, and the caller got whichever runtime the constructor had picked.
That is how `page` came to be called on a strategy that does not implement it.

Adding a strategy is one `register` call. Strategies do not share a constructor
signature -- the engine takes `threads` and `sparse_cache`, the lazy strategy
does not -- so `create` passes on only the keywords a strategy actually
declares, and a caller need not know which is which.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class StrategyEntry:
    """A named strategy: how to build it, and one line on what it is."""

    name: str
    factory: Callable[..., Any]
    description: str


_REGISTRY: dict[str, StrategyEntry] = {}

#: The name used when nobody asks for one.
DEFAULT = "engine"


def register(name: str, factory: Callable[..., Any], description: str = "") -> None:
    """Make `name` selectable. Re-registering the same name replaces it."""
    _REGISTRY[name] = StrategyEntry(name=name, factory=factory, description=description)


def available() -> dict[str, str]:
    """Name -> description, for `--help` and for error messages."""
    _ensure_registered()
    return {name: entry.description for name, entry in sorted(_REGISTRY.items())}


def create(name: str | None, /, **kwargs: Any) -> Any:
    """Build the strategy called `name`, passing on the keywords it accepts.

    Unknown names raise rather than falling back to the default: a typo that
    silently selects a different runtime is the failure mode this module
    exists to remove.
    """
    _ensure_registered()
    key = DEFAULT if name is None else name
    entry = _REGISTRY.get(key)
    if entry is None:
        choices = ", ".join(sorted(_REGISTRY)) or "<none registered>"
        raise ValueError(f"unknown execution strategy {key!r}; choose one of: {choices}")
    return entry.factory(**_accepted(entry.factory, kwargs))


def _accepted(factory: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Filter `kwargs` down to the parameters `factory` declares.

    A strategy that grows a parameter therefore starts receiving it with no
    change at any call site, and one that lacks it does not break.
    """
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):       # builtins, C callables
        return dict(kwargs)
    parameters = signature.parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in parameters}


def _ensure_registered() -> None:
    """Register the built-in strategies on first use.

    Done lazily and here rather than at package import: the engine strategy
    pulls in the whole engine package, and importing that to run the lazy
    strategy would make the choice cost something.
    """
    if _REGISTRY:
        return
    from voxlogica.execution_strategy.lazy import LazyExecutionStrategy

    def _engine(**kwargs: Any):
        from voxlogica.engine.strategy import EngineExecutionStrategy
        return EngineExecutionStrategy(**_accepted(EngineExecutionStrategy, kwargs))

    register("engine", _engine,
             "content-addressed scheduling engine with handle-based arguments (default)")
    register("lazy", LazyExecutionStrategy,
             "the earlier demand-driven strategy; kept for comparison, see issue #53")

    # SequentialExecutionStrategy is deliberately NOT registered. It is
    # unreferenced outside its own module and has no test, and it does not run:
    # `print "a" 2+3` reaches E_INVALID_ARGUMENT after a first, separate
    # AttributeError was removed from its `compile`. A name that always fails is
    # worse than a name that is absent, so it is not offered until someone
    # revives it -- at which point one `register` call here is the whole change.
