# The artifact as a container: the engine, its pinned dependencies, the BraTS
# cases and the trained weights, built once here so that nothing about the
# reviewer's machine can change what runs.
#
# Build (from the artifact root, where this file is):
#     docker build -t voxlogica-tacas .
#
# Run -- see the README for what each experiment expects. `out` keeps the
# written images on your disk, `store` keeps the engine's results store there
# too, so a second run is instant and the container stays small:
#     docker run --rm -v "$PWD/out:/artifact/output" -v "$PWD/store:/root/.voxlogica" \
#         voxlogica-tacas ./voxlogica run doc/gallery/programs/hello/hello-greyscale.imgql
#
# Experiment 4 needs the GPU, which means the NVIDIA Container Toolkit on the
# host and `--gpus all` (podman: `--device nvidia.com/gpu=all`).
FROM ubuntu:25.04

# What bootstrap.py needs, and nothing else: it fetches its own interpreter.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        python3 ca-certificates curl unzip git build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /artifact
COPY . /artifact

# Install at BUILD time, so the image a reviewer loads is already synchronized
# with the hashed lock files and needs no network to run. The uv cache is
# deleted in the same layer; uv hardlinks into .venv, so the environment keeps
# its files and the layer does not carry them twice.
ENV UV_CACHE_DIR=/tmp/uv-cache
RUN ./voxlogica --help > /dev/null && rm -rf /tmp/uv-cache

# The browser UI has nothing to talk to in here, and would bind a port.
ENV VOXLOGICA_NO_UI=1

CMD ["bash"]
