FROM docker.io/pytorch/pytorch:2.8.0-cuda12.9-cudnn9-runtime

# Hint for container runtimes (Podman) that GPU is desired
ENV NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=all

# Ensure non-interactive installs and preset timezone to avoid tzdata prompts
ARG DEBIAN_FRONTEND=noninteractive
ENV DEBIAN_FRONTEND=${DEBIAN_FRONTEND}
ENV TZ=Etc/UTC
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo "$TZ" > /etc/timezone \
    && echo "tzdata tzdata/Areas select Etc" | debconf-set-selections \
    && echo "tzdata tzdata/Zones/Etc select UTC" | debconf-set-selections

RUN echo "root:root" | chpasswd
RUN apt-get update
RUN apt-get -y dist-upgrade
RUN apt-get -y install --no-install-recommends software-properties-common
RUN apt-get update

RUN apt-get -y install --no-install-recommends \
    tzdata \
    git git-lfs htop screen unzip psmisc nvtop wget \
    libgl-dev libegl-dev libfreetype-dev libglib2.0-0 \
    ca-certificates curl gnupg python3 python3-pip python3-venv bash \
    && rm -rf /var/lib/apt/lists/*

# Install Python 3.13 and set it as default python3
RUN set -eux; \
    add-apt-repository -y ppa:deadsnakes/ppa; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3.13 python3.13-venv python3.13-dev; \
    /usr/bin/python3.13 -m ensurepip --upgrade; \
    /usr/bin/python3.13 -m pip install --upgrade pip setuptools wheel; \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 10; \
    update-alternatives --set python3 /usr/bin/python3.13; \
    ln -sf /usr/local/bin/pip3.13 /usr/local/bin/pip3 || true; \
    ln -sf /usr/local/bin/pip3.13 /usr/bin/pip3 || true

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

