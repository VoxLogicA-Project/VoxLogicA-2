#!/usr/bin/env python3
"""Thin pytest wrapper retained for compatibility."""

from __future__ import annotations

import sys

import pytest


if __name__ == "__main__":
    raise SystemExit(pytest.main(sys.argv[1:]))
