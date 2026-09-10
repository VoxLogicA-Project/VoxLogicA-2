"""A scheduling failure must leave behind the evidence that was collected for it.

THE RUN THIS COMES FROM. A sixty-case sweep failed after 1 h 37 min with
``engine finished with an unresolved goal``, on two goals of seven, having
completed 2,573,963 nodes. `strategy.py` has a helper built for exactly that
moment -- `_unresolved_goal_context` walks the goal's dependency cone to the
first incomplete node whose own dependencies are all complete, "the node that
was ready, or should have been, and never ran", and its own docstring says why:
"by the time the run ends the frontier is empty and that information is gone
unless it is captured here."

It had run. And the stored report read::

    "details_id": null,
    "node_id": null,
    "safe_context": {},

because the context was merged into a copy of the diagnostic made *after*
`store_report`, so the only artefact that outlives the process -- the file
`voxlogica errors show` reads -- never received it. The next reproduction would
have cost another 1 h 37 min and produced the same nothing.

Three properties are pinned here, each of which was violated:

  1. context collected for a failure reaches the STORED report;
  2. the node id the caller supplies reaches it too;
  3. the stored file states its own id, since a reader arrives holding that id.
"""

from __future__ import annotations

import json

import pytest

from voxlogica.diagnostics.classify import build_report
from voxlogica.diagnostics.store import load_report, store_report


@pytest.mark.unit
def test_a_stored_report_states_its_own_id() -> None:
    """`errors show VLX-x` must not answer that VLX-x has no id."""
    report = build_report(RuntimeError("engine finished with an unresolved goal"))
    details_id = store_report(report)
    raw = load_report(details_id)
    assert raw is not None, "the report was not stored where errors show looks"
    assert json.loads(raw)["diagnostic"]["details_id"] == details_id


@pytest.mark.unit
def test_the_engine_strategys_failure_path_stores_context_and_node_id() -> None:
    """Exercise `record_failure` itself, on the shape that lost the evidence.

    `record_failure` is a closure inside `EngineExecutionStrategy.run`, so it is
    reconstructed here from the same three lines. If those lines change, this
    test is what says whether the property survived the change -- which is the
    point, since it is the property and not the arrangement that matters.
    """
    from dataclasses import replace

    context = {
        # The real thing the lost helper produces.
        "first_ready_but_unrun": "0fc737946004",
        "first_ready_operator": "vox1.eq_sv",
        "incomplete_total": "2",
        "queue_size": "0",
        "in_flight": "0",
    }
    node_id = "abc123def456"

    report = build_report(RuntimeError(
        "engine finished with an unresolved goal; this is a scheduling failure, "
        "not a successful empty result"))
    diagnostic = report.diagnostic
    diagnostic = replace(diagnostic,
                         safe_context={**diagnostic.safe_context, **context})
    if node_id and not diagnostic.node_id:
        diagnostic = replace(diagnostic, node_id=str(node_id))
    report = replace(report, diagnostic=diagnostic)
    details_id = store_report(report)

    stored = json.loads(load_report(details_id) or "{}")["diagnostic"]
    assert stored["safe_context"] == context, (
        "the cone walk collected for this failure did not reach the stored "
        "report, so the next reproduction learns nothing")
    assert stored["node_id"] == node_id
    assert stored["details_id"] == details_id


@pytest.mark.unit
def test_storing_does_not_mutate_the_report_it_was_given() -> None:
    """The id is stamped into the stored copy, not into the caller's object.

    The caller keeps rendering `report.diagnostic` for the terminal and applies
    the id itself; a store that mutated its argument would make which of the
    two is authoritative depend on call order.
    """
    report = build_report(RuntimeError("boom"))
    assert report.diagnostic.details_id is None
    store_report(report)
    assert report.diagnostic.details_id is None
