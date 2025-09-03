FROM python:3.13-slim-bullseye

ENV DEBIAN_FRONTEND=noninteractive

# Install minimal build tools and helper packages. Keep distro default compilers
# (older, stable toolchain) to avoid Triton/Inductor compiler issues.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    build-essential git curl wget ca-certificates unzip pkg-config \
    python3-venv python3-distutils \
    && python3 -m pip install --upgrade pip setuptools wheel \
    && rm -rf /var/lib/apt/lists/*

# create a non-root user for development
RUN useradd -ms /bin/bash devuser && mkdir -p /home/devuser/workspace && chown -R devuser:devuser /home/devuser
USER devuser
WORKDIR /home/devuser/workspace

# Copy sitecustomize to site-packages so it's picked up on Python startup
COPY --chown=devuser:devuser .devcontainer/sitecustomize.py /usr/local/lib/python3.13/site-packages/sitecustomize.py

# Also set environment variables at container level to disable torch dynamic compilation
ENV TORCH_COMPILE_DISABLE=1 \
    TORCHDYNAMO_DISABLE=1 \
    TORCH_DYNAMO_DISABLE=1 \
    PYTORCH_DISABLE_CUDNN_COMPILATION=1
