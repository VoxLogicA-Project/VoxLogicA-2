# Glioblastoma Target Volumes (GTV / CTV / PTV)

Summary of the two current consensus sources for WHO grade 4 adult-type diffuse glioma / glioblastoma radiotherapy contouring.

---

## ESTRO–EANO 2023

**Title:** ESTRO–EANO guideline on target delineation and radiotherapy details for glioblastoma  
**Authors:** Niyazi et al.  
**Journal:** *Radiotherapy and Oncology* 184 (2023) 109663  
**DOI:** [10.1016/j.radonc.2023.109663](https://doi.org/10.1016/j.radonc.2023.109663)  
**Local PDF:** [originals/estro-eano-2023-glioblastoma-contouring.pdf](./originals/estro-eano-2023-glioblastoma-contouring.pdf)  
**Full text:** [The Green Journal](https://www.thegreenjournal.com/article/S0167-8140(23)00201-3/fulltext)

Most current **contouring-specific** European consensus. Updates the prior EORTC-based approach.

### GTV

- Postoperative **contrast-enhanced T1 MRI**.
- **Resection cavity** plus any **residual contrast-enhancing tumour**.
- Biopsy-only patients: T1 contrast-enhancing tumour.
- **Do not** include peri-tumoural oedema in the GTV.
- Exclude regions of enhancement that represent post-surgical infarction or gliosis (compare pre- and immediate post-resection MRI).

### CTV

- **GTV + 15 mm** isotropic margin in three dimensions, **edited for anatomical barriers** to microscopic spread.
- **Oedema is not routinely included.** T2/FLAIR hyperintensity may represent non-enhancing tumour; include suspicious regions in the CTV when infiltration is suspected (no fixed margin consensus for FLAIR-only disease; panel range 0–15 mm).
- **Barrier editing (Delphi consensus):**
  - Skull: **0 mm** (bone window)
  - Ventricles: **5 mm**
  - Falx, tentorium cerebelli: **0 mm**
  - Optic pathways / chiasm, brainstem: **0 mm** when tumour is distant from relevant white-matter tracts
  - **No reduction** at corpus callosum, cerebral peduncles, cerebellar peduncles
- Subventricular zone: **not** intentionally included (82% Delphi against).
- Molecularly defined glioblastoma: similar **10–15 mm** range; optimal margin strategy still under study.

### PTV

- Department-specific, based on fixation, setup verification, and IGRT audit.
- **Usually ≤ 3 mm** with daily IGRT and modern delivery; default **3 mm** if local data unavailable.
- Thermoplastic mask + daily IGRT recommended; 6D correction when available.

### Other key points

- Single-phase plan (no mandatory cone-down); EORTC-style cavity + residual enhancement + 15 mm CTV.
- Standard dose: **60 Gy in 2 Gy fractions** (good-performance adults); hypofractionated schedules for elderly use same CTV/PTV definitions.
- Planning MRI: contrast-enhanced 3D T1 and T2/FLAIR; 3D sequences help with residual non-enhancing tumour.
- IMRT/VMAT preferred over 3D-CRT when OAR proximity demands conformality.

### Comparison with prior EORTC recommendation

| Aspect | Prior EORTC | ESTRO–EANO 2023 |
|--------|-------------|-----------------|
| CTV margin | 20 mm | **15 mm** |
| FLAIR / oedema | Optional inclusion | **Exclude vasogenic oedema**; include suspected non-enhancing tumour |
| PTV | 3–5 mm | **3 mm advised** (≤ 3 mm with IGRT) |

---

## ASTRO 2025

**Title:** Radiation Therapy for WHO Grade 4 Adult-Type Diffuse Glioma: An ASTRO Clinical Practice Guideline  
**Journal:** *Practical Radiation Oncology* (2025)  
**DOI:** [10.1016/j.prro.2025.05.014](https://doi.org/10.1016/j.prro.2025.05.014)  
**Local PDF:** *not yet in repo* — see [originals/README.md](./originals/README.md) for manual download  
**Journal PDF:** [Practical Radiation Oncology](https://www.practicalradonc.org/action/showPdf?pii=S1879850025001638)  
**PubMed:** [40578479](https://pubmed.ncbi.nlm.nih.gov/40578479/)  
**Patient summary (PDF):** [ASTRO](https://www.astro.org/getmedia/ff0d6a20-3d88-4713-8710-e24c1efddc11/WHOGrade4Glioma_PC.pdf)

Latest **broad radiotherapy** guideline for WHO grade 4 adult-type diffuse glioma. Accepts either a **single-phase** plan or a **cone-down / boost** approach.

### Option A — Cone-down / boost desired

| Volume | Definition |
|--------|------------|
| **GTV1** | Resection cavity + residual postoperative T1 post-contrast enhancement + **T2/FLAIR changes** (non-enhancing tumour) |
| **GTV2** | Resection cavity + residual postoperative T1 post-contrast enhancement |
| **CTV1/2** | GTV1/2 + **10–20 mm**, modified for natural barriers (bone, dura, etc.) |
| **PTV1/2** | CTV1/2 + **3–5 mm** |

### Option B — No cone-down / boost (single phase)

| Volume | Definition |
|--------|------------|
| **GTV** | Resection cavity + residual T1 post-contrast enhancement |
| **CTV** | GTV + **10–20 mm**, with additional expansion as needed to include **T2/FLAIR signal changes** (non-enhancing tumour), modified for natural barriers |
| **PTV** | CTV + **3–5 mm** |

### Supporting recommendations

- **IMRT (including VMAT)** over 3D-CRT to reduce toxicity (strong).
- **Volumetric brain MRI** with and without contrast, preferably **≤ 14 days** before RT (strong).
- **Daily image guidance** to enable reduced CTV-to-PTV margins (strong).
- Concurrent TMZ → adjuvant TMZ after biopsy/resection (strong); alternating electric fields conditionally recommended.

---

## Practical synthesis

| Parameter | ESTRO–EANO 2023 | ASTRO 2025 |
|-----------|-----------------|------------|
| GTV core | Cavity + T1 enhancement | Cavity + T1 enhancement |
| CTV margin | **15 mm** (fixed recommendation) | **10–20 mm** (range) |
| T2/FLAIR / oedema | Not routine; include if suspected non-enhancing tumour | Include T2/FLAIR (non-enhancing tumour) in CTV or GTV1 |
| PTV | **≤ 3 mm** with IGRT | **3–5 mm** |
| Phase design | Single phase | Single phase **or** cone-down/boost |

For VoxLogicA / BraTS-style work, note that BraTS labels (enhancing tumour, necrosis, oedema) map loosely to clinical volumes but **clinical CTV/PTV rules are not identical** to BraTS segmentation classes.

---

## Trial and regional context

- **Europe:** EORTC and ESTRO–EANO guidelines remain the most widely cited family.
- **North America / cooperative trials:** RTOG, NRG Oncology, and ASTRO documents; **always follow the active trial protocol** (e.g. NRG BN001, legacy RTOG margins) when treating on study.
- **Recommended citation pair for a current non-trial protocol:** ASTRO 2025 + ESTRO–EANO 2023.

---

## References

1. Niyazi M, Andratschke N, Bendszus M, et al. ESTRO-EANO guideline on target delineation and radiotherapy details for glioblastoma. *Radiother Oncol*. 2023;184:109663. doi:[10.1016/j.radonc.2023.109663](https://doi.org/10.1016/j.radonc.2023.109663)
2. Yeboa DN, Braunstein SE, Cabrera A, et al. Radiation Therapy for WHO Grade 4 Adult-Type Diffuse Glioma: An ASTRO Clinical Practice Guideline. *Pract Radiat Oncol*. 2025. doi:[10.1016/j.prro.2025.05.014](https://doi.org/10.1016/j.prro.2025.05.014)
