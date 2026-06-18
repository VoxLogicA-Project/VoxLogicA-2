# nnUNet integration

Implementation: `implementation/python/voxlogica/primitives/nnunet/`

User docs: `doc/user/nnunet-namespace.md`

Gallery: `doc/gallery/programs/nnunet/`

E2e test: `tests/e2e/nnunet_shapes/`

The previous Dask-bag wrapper sketch (`nnunet_wrapper.py`) was removed; training uses in-memory case sequences materialized to nnU-Net's on-disk layout.
