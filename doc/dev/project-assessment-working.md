# VoxLogicA-2 Project Assessment (Working Document)

## Scope
This document tracks current module assessment status and review findings.

## Baseline Classification
The following baseline decisions are accepted as current direction:

- `main.py`: **Fine**
- `parser.py`: **Fine**
- `reducer.py`: **Fine**
- `lazy.py`: **Needs rewrite**
- `execution.py`: **Needs rewrite**
- `storage.py`: **Needs rewrite**

All other modules are in the "check and validate" bucket.

## Other Modules Checked
Checked modules:

- `features.py`
- `version.py`
- `__init__.py`
- `converters/__init__.py`
- `converters/json_converter.py`
- `converters/dot_converter.py`
- `primitives/default/*`
- `primitives/arrays/__init__.py`
- `primitives/simpleitk/__init__.py`
- `primitives/nnunet/__init__.py`
- `primitives/test/*`
- `stdlib/stdlib.imgql`
- `static/index.html` (surface check only)

Validation performed:

- Syntax validation (`py_compile`) across checked Python modules: passed.
- Static review for correctness and maintainability risks: completed.

## Findings (Other Modules)
Severity legend: `High`, `Medium`, `Low`.

1. `Medium` - Potential division-by-zero/NaN output in Dice metric
 - File: `implementation/python/voxlogica/primitives/arrays/__init__.py`
 - Lines: around `210`
 - Detail: `dice = (2.0 * intersection) / (np.sum(pred_binary) + np.sum(gt_binary))` has no zero-denominator guard when both masks are empty.

2. `Low` - Dead/unused memory assignment option in run flow
 - File: `implementation/python/voxlogica/features.py`
 - Lines: option declared at `93`, but no usage in handler logic.
 - Detail: `compute_memory_assignment` is wired through CLI/API but not consumed in execution result building.

3. `Low` - Duplicate deduplication logging and tight internal coupling
 - File: `implementation/python/voxlogica/primitives/default/for_loop.py`
 - Lines: duplicate log block around `143-151`; direct write to `engine.storage._memory_cache` at `105`.
 - Detail: repeated dedup logs increase noise; direct access to private storage internals increases fragility.

4. `Low` - Disabled CPU-forcing branch left as hardcoded dead code
 - File: `implementation/python/voxlogica/primitives/nnunet/__init__.py`
 - Line: around `412`
 - Detail: `if False and device in ("cpu", "none")` indicates intentionally disabled behavior with TODO note.

5. `Low` - Redundant local helper in JSON converter
 - File: `implementation/python/voxlogica/converters/json_converter.py`
 - Lines: local `unwrap()` defined around `40-50` but not used.
 - Detail: minor maintainability issue; behavior is still correct through `WorkPlanJSONEncoder()._unwrap(...)`.

## Overall Status of "Other Modules"
- No immediate blockers found that require full-module rewrite.
- Several low/medium cleanup issues should be queued for hardening.
- `primitives/*` quality is mixed by design (production + test/demo primitives in same area); keep this in mind when enforcing quality gates.

## Recommended Next Pass
1. Patch the `dice_score` denominator guard.
2. Either implement or remove `compute_memory_assignment` plumbing.
3. Clean `for_loop` internals (remove duplicate logs; avoid direct private cache writes).
4. Resolve or remove dead nnUNet CPU branch.
5. Remove redundant helper code in JSON converter.
