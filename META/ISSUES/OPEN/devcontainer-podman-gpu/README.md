# Devcontainer on Podman: GPU base image and host lib bind mount issue

Date: 2025-08-17
Status: Open

## Summary
- VS Code Dev Containers used Podman as Docker shim. Base image was `pytorch/pytorch:2.8.0-cuda12.9-cudnn9-runtime`.
- Devcontainer config bind-mounted host `/usr/lib/x86_64-linux-gnu` and `/usr/bin/nvidia-smi` into the container to expose NVIDIA libs.
- This clobbered the container userland and caused `grep: error while loading shared libraries: libpcre.so.3` during VS Code server requirement check.

## Root Cause
- Host system library bind mount replaced container libraries, causing ABI mismatch (Ubuntu 22.04 containers expect PCRE2, not `libpcre.so.3`).

## Fix Implemented
- Edited `.devcontainer/devcontainer.json` to remove the two problematic mounts.
- Retained GPU device mappings and NVIDIA env vars.
- Recommend using NVIDIA Container Toolkit OCI hook for Podman instead of bind-mounting host libs.

## Verification
- Devcontainer JSON validated (no syntax errors).
- Expect VS Code server startup to succeed; `grep` should work.

## Next Steps
- Rebuild devcontainer in VS Code.
- Ensure NVIDIA Container Toolkit is installed so Podman exposes GPUs without manual lib mounts.
- Optional: Add docs snippet under `doc/user/` about Podman + NVIDIA setup.
