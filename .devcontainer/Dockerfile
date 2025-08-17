FROM docker.io/pytorch/pytorch:2.8.0-cuda12.9-cudnn9-runtime

# Hint for container runtimes (Podman) that GPU is desired
ENV NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=all

RUN echo "root:root" | chpasswd
RUN apt update
RUN apt -y dist-upgrade
RUN apt -y install software-properties-common
RUN apt update

RUN apt -y install git git-lfs htop screen unzip psmisc nvtop wget libgl-dev libegl-dev libfreetype-dev libglib2.0-0 ca-certificates curl gnupg python3 python3-pip python3-venv bash

# Install NVIDIA utilities to provide nvidia-smi if available in the host via container hooks
# Try to install nvidia-utils, falling back gracefully if not available (keeps build green on non-NVIDIA hosts)
RUN set -eux; \
    if apt-cache show nvidia-utils-535 >/dev/null 2>&1; then \
    apt-get update && apt-get install -y --no-install-recommends nvidia-utils-535; \
    elif apt-cache show nvidia-utils-550 >/dev/null 2>&1; then \
    apt-get update && apt-get install -y --no-install-recommends nvidia-utils-550; \
    elif apt-cache show nvidia-utils >/dev/null 2>&1; then \
    apt-get update && apt-get install -y --no-install-recommends nvidia-utils; \
    else \
    echo "nvidia-utils not found in apt cache, relying on host-provided hooks"; \
    fi; \
    true

# Ensure bash is the default shell in the container
SHELL ["/bin/bash", "-lc"]

