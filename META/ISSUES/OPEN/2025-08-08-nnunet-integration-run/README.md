# Run revised nnUNet prompt and workflow

Date: 2025-08-08
Owner: AI Agent

Summary:
- Research nnUNet v2 docs and codebase contexts
- Implement real nnUNet integration (train_directory, predict)
- Revise PROMPT_FOR_BETTER_AI.md to match real implementation
- Attempt to run end-to-end synthetic workflow using ./voxlogica

Context:
- Changes in implementation/python/voxlogica/primitives/nnunet/__init__.py to call nnUNetv2 CLIs
- PROMPT_FOR_BETTER_AI.md updated to reflect env vars, CLI usage
- Synthetic data generator available at tests/test_nnunet_synthetic/generate_synthetic_data.py

Acceptance Criteria:
- Training and prediction use real nnUNetv2 commands
- Returns contain model path and produced prediction files
- No syntax/type errors in modified files

Next Steps:
1. Verify nnUNetv2 and PyTorch available in active venv
2. Generate synthetic dataset (20 images)
3. Run imgql workflow tests referencing train_directory and predict
4. Capture logs and artifacts under /tmp/nnunet_synthetic_* and work_dir

Dependency Update (2025-08-09):
- Added nnunetv2>=2.4.0 to implementation/python/requirements.txt
- Added conditional torch (python_version < 3.13) to avoid install failures on unsupported interpreter
- Created META/nnunetv2-dependency-notes.md with environment guidance

Blockers/Risks:
- nnUNetv2 and PyTorch may not be installed in the project venv
- Training time can be long; consider small nfolds or 2d configuration
