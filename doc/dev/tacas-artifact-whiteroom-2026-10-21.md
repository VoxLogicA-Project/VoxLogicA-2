# TACAS artifact: independent white-room review, 2026-10-21

**Rules:** TACAS 2027 AE, https://etaps.org/2027/conferences/ae-tacas/ (2027 call is out). Reference VM: Ubuntu 26.04, Zenodo 23472158. **Environment emulated:** the OVA could not be booted (no `/dev/kvm` access, no qemu, no sudo). Instead I used a podman container `ubuntu:26.04` with exactly the VM's listed packages (build-essential git curl cmake clang mono-complete default-jdk python3-full python3-pip ruby cargo rustc; plus `unzip`). Network was on for the bootstrap only. Experiment 4 (GPU) ran on the host with a fresh `HOME`. Logs are in `fmt-5000:~/tacas-whiteroom/`: `c1.log` (24 cores), `c2.log` (`--cpus 4 --memory 8g`), `c4.log` (host GPU), `off.log` / `reuse*.log` (`--network none`).

| step / experiment | expected | measured | result |
|---|---|---|---|
| Smoke 1–5: checksums, bootstrap, 6 programs well-formed, range-map, hello world | 0 failures, "hashed lock files", 6 ok, exact strings | all identical; bootstrap 114 s (README: 44 s) | pass |
| Smoke 6: perf suite | Problems: `- none` | only "foreign load", caused by my parallel containers | pass (environment) |
| B: dataset manifest `--subset` | OK, exit status 0 | OK, exit status 0 | pass |
| Exp 1: nnU-Net on synthetic circles, 24 cores | exit 0, 14 files, 51 min, 1.2 GB | exit 0, 14 files, **80.5 min**, 2.1 GB | pass (slower than claimed) |
| Exp 2: five BraTS cases, 4 cpu / 8 GB | 6 Dice values exact, 1 file, 31 s | all exact, 1 file, 22 s, 5.4 GB | pass |
| Exp 2 again, offline, in a fresh container | same values | `average_best` identical, 19 s | pass |
| Exp 3: threshold sweep, 4 cpu / 8 GB | 11 goals exact, 90 distinct PNGs, ≤ 3 min | all exact, 90/90 distinct, 109 s, **7.8 GB peak** | pass |
| Exp 4: sweep against nnU-Net, host GPU, `--threads 4` | Dice to 1e-4, thresholds exact, 120 distinct PNGs, 77 s, 7.6 GB | all within 2e-6, thresholds exact, 120 files but **117 distinct**, 102 s, 6.3 GB; `model/` unchanged | pass |
| Section H: `list-primitives`, `typecheck`, edit hello to use `green` | rc 0, max = 255 | rc 0, max = 255.0 | pass |

**Blockers**
- The DOI is not registered. `https://doi.org/10.5281/zenodo.23340242` returns 404, and the Zenodo API says "persistent identifier is not registered". Without a resolving DOI, Available is denied. Checked on 2026-10-21 with curl and WebFetch.

**Minor issues**
- Exp 1 took 80.5 min against the README's 51 min on 24 cores, and peaked at 2.1 GB against 1.2 GB (`c1.log`, MEAS line). The README's time estimates should be loosened.
- Exp 3 at the default thread count peaked at 7.8 GB inside an 8 GB limit. The README's "8 GB runs experiments 1–3" holds only if the engine sees 4 cores; a cgroup limit still exposes 24 (`c2.log`).
- Exp 4: the case 069 overlays are identical to the plain FLAIR wherever the mask is empty, which contradicts "120 DIFFERENT images". The output itself is plausible (`c4/.../output/aiim-sweep-nnunet/case_069_*`).
- The README says goals are "printed as JSON"; they are actually printed as `name=value` lines, followed by a JSON summary. Exp 2 also prints matrices and `Image (0x…)` reprs (`exp2.out`).
- The bootstrap downloads about 3 GB, including CUDA torch, even for runs that use no GPU. The 2027 call allows this; the VM's Zenodo page asks for self-contained artifacts.
- The data shipped is BraTS 2020: 20 cases plus a 5-case sample. The README credits the challenge but states no redistribution licence for the data.

**Verdict**
- Available: **denied as submitted**, because the DOI does not resolve. It becomes a pass once the Zenodo record is published.
- Functional: **pass**. Every stated value was reproduced, exactly or within the stated tolerance.
- Reusable: **pass**. GPL-3.0, pinned and hash-locked dependencies, a working extension path in section H, and a usable performance suite.
