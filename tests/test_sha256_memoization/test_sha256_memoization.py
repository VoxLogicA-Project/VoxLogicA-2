#!/usr/bin/env python3
"""
Verifies SHA256-based content-addressed IDs and memoization in the reducer. Ensures that equivalent operations produce the same ID, different operations produce different IDs, and that memoization prevents duplicate computation. Also checks argument order consistency and SHA256 ID properties.
"""
description = """Verifies SHA256-based content-addressed IDs and memoization in the reducer. Ensures that equivalent operations produce the same ID, different operations produce different IDs, and that memoization prevents duplicate computation. Also checks argument order consistency and SHA256 ID properties."""

# ...existing code from old test_sha256_memoization.py...
