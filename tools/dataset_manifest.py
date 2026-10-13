#!/usr/bin/env python3
"""Pin which BraTS cases an experiment reads, and in which order.

Both BraTS programs now name their cases from the dataset's own
`name_mapping.csv` -- the HGG rows, offset by `case_start`/`eval_start` -- so a
missing or extra case no longer renumbers anyone else's. What still decides the
population is that CSV, and what still decides the data is the bytes behind
each name. A copy of BraTS with a different `name_mapping.csv`, or with one
case's volume replaced, returns numbers that cannot be compared with ours and
says nothing about it: the sweep succeeds, the Dice values are plausible, and
they answer another question.

This writes down the order, and checks it.

    python3 tools/dataset_manifest.py --write <brats-root>   # ours, once
    python3 tools/dataset_manifest.py <brats-root>           # theirs, to verify
    python3 tools/dataset_manifest.py --quick <brats-root>   # sizes only, no hashing

Stdlib only, and plain `python3`: a reviewer runs this BEFORE building anything.
"""

from __future__ import annotations

import argparse
import fnmatch
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent / "brats2020-manifest.json"

#: Exactly the patterns the shipped programs pass to `dir`. Recursive, full
#: paths, sorted -- the same three flags, so this reproduces what they index.
GLOBS = ["*_flair.nii.gz", "*_t1.nii.gz", "*_t1ce.nii.gz", "*_t2.nii.gz", "*_seg.nii.gz"]

#: Files the programs read that are NOT images: the metadata that decides which
#: cases are in the study at all. The nnU-Net sweep selects the HGG cases from
#: name_mapping.csv, so a different copy of that file is a different
#: population, silently. Pinned by hash like everything else.
METADATA = ["name_mapping.csv"]

#: Read in blocks: the dataset is ~3 GB and a reviewer should not need the RAM.
_BLOCK = 1 << 20


def _walk(root: Path, pattern: str):
    """Every file under root matching pattern, symlinked directories included.

    os.walk, not Path.rglob: rglob only learned to follow symlinked directories
    in Python 3.13 (`recurse_symlinks=`), and this tool runs on the SYSTEM
    python3 -- before bootstrap has built anything -- which on the TACAS VM is
    3.10. It crashed there, on the very first thing the README asks for.
    """
    for dirpath, _dirs, files in os.walk(root, followlinks=True):
        for name in files:
            if fnmatch.fnmatch(name, pattern):
                yield Path(dirpath) / name


def listing(root: Path, pattern: str) -> list[str]:
    """The order `dir(root, pattern, true, true)` produces, named relative to root.

    THIS SORTS THE WAY `dir` SORTS, which is not the obvious way. `dir` with
    full_paths builds `str(entry.resolve())` and sorts THAT (primitives/default/
    dir.py), so when the dataset is a farm of symlinks the order follows the
    TARGETS, not the names under the root. A reviewer who links each case from a
    different place can therefore get the cases in an order their directory
    listing does not show -- and since the programs index by position, that is a
    silent reshuffle of which case is which. `check` warns when the two orders
    disagree.

    Names are reported relative to the root so two different roots compare.
    """
    pairs = [(str(item.resolve()), item.relative_to(root).as_posix())
             for item in _walk(root, pattern)]
    pairs.sort(key=lambda pair: pair[0])          # exactly what dir does
    return [rel for _resolved, rel in pairs]


def naive_listing(root: Path, pattern: str) -> list[str]:
    """The order the directory names suggest, for comparison with the real one."""
    return sorted(item.relative_to(root).as_posix() for item in _walk(root, pattern))


#: Bumped when what the numbers MEAN changes, so an old manifest is refused
#: instead of compared against the wrong thing. 2: `.nii.gz` files are
#: identified by their decompressed content.
MANIFEST_VERSION = 2


def content(path: Path) -> tuple[str, int]:
    """sha256 and length of what the file CONTAINS, not of the file.

    For a `.nii.gz` that means the NIfTI inside, read through gzip. The reason
    is the acquisition path: the public mirrors of BraTS 2020 ship the volumes
    UNCOMPRESSED, so a reviewer gzips them, and gzip is not reproducible --
    the header carries a name and a timestamp, and the level is a choice.
    Measured on one case: the official file is 1782235 bytes, `gzip` gives
    1784262 and `gzip -9` gives 1770718, three different digests for identical
    data. Hashing the bytes would therefore report every file of a correctly
    obtained dataset as wrong, which is worse than not checking.

    The decompressed length is returned too, and stored as `size`, for the same
    reason: the compressed size is not a property of the data.
    """
    digest = hashlib.sha256()
    total = 0
    opener = gzip.open if path.suffix == ".gz" else (lambda p, mode: p.open(mode))
    with opener(path, "rb") as handle:
        for block in iter(lambda: handle.read(_BLOCK), b""):
            digest.update(block)
            total += len(block)
    return digest.hexdigest(), total


def hgg_ids(root: Path) -> list[str]:
    """The selection the nnU-Net program makes, reproduced: grade first field,
    2020 id last, rows in file order. So the checker can say not just "the CSV
    differs" but "and here is the first case it would pick differently"."""
    path = root / "name_mapping.csv"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split(",")
        if fields and fields[0] == "HGG":
            out.append(fields[-1])
    return out


def _entry(full: Path, *, quick: bool) -> dict:
    """What we record for one file. With --quick, the size only -- and for a
    `.gz` that still means the decompressed size, so a quick check stays
    meaningful on a recompressed copy; it costs a read either way."""
    if quick:
        if full.suffix == ".gz":
            return {"size": content(full)[1]}
        return {"size": full.stat().st_size}
    digest, size = content(full)
    return {"size": size, "sha256": digest}


def survey(root: Path, *, quick: bool) -> dict:
    order = {pattern: listing(root, pattern) for pattern in GLOBS}
    files: dict[str, dict] = {}
    for rel in METADATA:
        full = root / rel
        if full.is_file():
            entry = _entry(full, quick=quick)
            files[rel] = entry
    for paths in order.values():
        for rel in paths:
            if rel in files:
                continue
            full = root / rel
            files[rel] = _entry(full, quick=quick)
    return {"order": order, "files": files, "hgg_ids": hgg_ids(root)}


def write(root: Path, quick: bool) -> int:
    data = survey(root, quick=quick)
    counts = {p: len(v) for p, v in data["order"].items()}
    MANIFEST.write_text(json.dumps({
        "dataset": root.name,
        "note": "Generated by tools/dataset_manifest.py. The `order` lists are what "
                "`dir(root, pattern, true, true)` returns, relative to the root: the "
                "programs index these by position, so the ORDER is the contract.",
        "case_count": len(data["order"]["*_flair.nii.gz"]),
        "hgg_count": len(data["hgg_ids"]),
        "counts": counts,
        "manifest_version": MANIFEST_VERSION,
        "hashed": not quick,
        **data,
    }, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST}")
    for pattern, n in counts.items():
        print(f"  {pattern:18} {n}")
    print(f"  {len(data['files'])} files, "
          f"{'sizes only' if quick else 'sha256 for each'}")
    print(f"  name_mapping.csv: {len(data['hgg_ids'])} HGG cases")
    return 0


def check(root: Path, quick: bool) -> int:
    if not MANIFEST.is_file():
        sys.exit(f"no manifest at {MANIFEST}")
    want = json.loads(MANIFEST.read_text(encoding="utf-8"))
    version = want.get("manifest_version", 1)
    if version != MANIFEST_VERSION:
        sys.exit(f"{MANIFEST.name} is version {version}, this tool writes and reads "
                 f"version {MANIFEST_VERSION}. Version 1 identified `.nii.gz` files "
                 f"by their compressed bytes, which no recompressed copy can match; "
                 f"regenerate it with --write against the reference dataset.")
    if quick or not want.get("hashed"):
        quick = True
    problems: list[str] = []

    print(f"dataset:  {root}")
    print(f"expected: {want['dataset']}, {want['case_count']} cases\n")

    for rel in METADATA:
        if not (root / rel).is_file():
            problems.append(f"missing: {rel} -- the nnU-Net sweep selects its cases "
                            f"from it and cannot run without it")
            print(f"  WRONG {rel:18} missing")
            continue
        got, exp = hgg_ids(root), want.get("hgg_ids", [])
        if got == exp:
            print(f"  OK    {rel:18} {len(got)} HGG cases, in the expected order")
        else:
            limit = min(len(got), len(exp))
            first = next((i for i in range(limit) if got[i] != exp[i]), limit)
            problems.append(
                f"{rel}: selects {len(got)} HGG cases, expected {len(exp)}; first "
                f"difference at HGG INDEX {first} -- expected "
                f"{exp[first] if first < len(exp) else '(nothing)'}, found "
                f"{got[first] if first < len(got) else '(nothing)'}. This file "
                f"decides the population of the nnU-Net experiment.")
            print(f"  WRONG {rel:18} {len(got)} HGG cases (expected {len(exp)}), "
                  f"diverges at HGG index {first}")

    for pattern in GLOBS:
        expected = want["order"][pattern]
        found = listing(root, pattern)
        if found != naive_listing(root, pattern):
            problems.append(
                f"{pattern}: `dir` will order these files by the path each symlink "
                f"RESOLVES TO, and that order differs from the directory names. "
                f"Replace the symlinks with a copy, or link whole case directories "
                f"from one place.")
            print(f"  WARN  {pattern:18} symlink targets reorder this listing")
        if found == expected:
            print(f"  OK    {pattern:18} {len(found)} files, in the expected order")
            continue
        # Say WHICH position first diverges: that is the index the programs use,
        # so it names the case a reviewer would silently have evaluated instead.
        limit = min(len(found), len(expected))
        first = next((i for i in range(limit) if found[i] != expected[i]), limit)
        problems.append(
            f"{pattern}: {len(found)} files, expected {len(expected)}; "
            f"first difference at INDEX {first} -- "
            f"expected {expected[first] if first < len(expected) else '(nothing)'}, "
            f"found {found[first] if first < len(found) else '(nothing)'}")
        print(f"  WRONG {pattern:18} {len(found)} files (expected {len(expected)}), "
              f"diverges at index {first}")

    if not problems:
        checked = 0
        for rel, entry in sorted(want["files"].items()):
            full = root / rel
            if not full.is_file():
                problems.append(f"missing: {rel}")
                continue
            if quick:
                size = content(full)[1] if full.suffix == ".gz" else full.stat().st_size
                if size != entry["size"]:
                    problems.append(f"size differs: {rel}")
                    continue
            else:
                digest, size = content(full)
                if size != entry["size"]:
                    problems.append(f"size differs: {rel}")
                    continue
                if digest != entry["sha256"]:
                    problems.append(f"content differs: {rel}")
            checked += 1
        print(f"\n  {checked} files checked "
              f"({'size only' if quick else 'size and sha256'})")

    if problems:
        print(f"\nFAILED: {len(problems)} problem(s)\n")
        for problem in problems[:20]:
            print(f"  {problem}")
        if len(problems) > 20:
            print(f"  ... and {len(problems) - 20} more")
        print("\nThe experiments select cases by POSITION, so a listing that differs\n"
              "at index i means every program that reads case i onwards is reading\n"
              "different data than we did. The numbers it prints are not comparable\n"
              "with the expected values in the README.")
        return 1

    print("\nOK: this dataset matches the one the expected values were measured on.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", help="the BraTS 2020 training directory")
    ap.add_argument("--write", action="store_true",
                    help="write the manifest from this root instead of checking against it")
    ap.add_argument("--quick", action="store_true",
                    help="compare sizes only, skipping ~3 GB of hashing")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")
    return write(root, args.quick) if args.write else check(root, args.quick)


if __name__ == "__main__":
    raise SystemExit(main())
