# VoxLogicA-2 Documentation & Domain Knowledge

Welcome to the VoxLogicA-2 documentation hub. This space is designed to provide clear, professional, and accessible domain knowledge for colleagues at CNR, academic collaborators, and the broader research community.

## Project Overview

**VoxLogicA-2** is the next-generation spatial model checker for declarative image analysis, building on the success of VoxLogicA 1. The project aims to provide a modular, high-performance, and explainable platform for medical imaging and related domains, with a focus on flexibility, scientific rigor, and reproducibility.

- **Lead Institution:** ISTI-CNR, Pisa, Italy
- **Main Contacts:** Vincenzo Ciancia, Laura Bussi, et al.
- **Repository:** [VoxLogicA-2 (official, up-to-date)](https://github.com/voxlogica-project/VoxLogicA-2)

## Domain Knowledge Initialization

This documentation is intended as a living knowledge base for:

- Technical summaries and references
- Key scientific papers and their relevance
- Design decisions and architectural notes
- Onboarding for new contributors and collaborators

---

## Key Scientific Papers

### 1. VoxLogicA 1 (Springer, TACAS 2019)

- **Title:** VoxLogicA: A Spatial Model Checker for Declarative Image Analysis
- **Authors:** Gina Belmonte, Vincenzo Ciancia, Diego Latella, Mieke Massink
- **PDF:** [Springer Link](https://link.springer.com/content/pdf/10.1007/978-3-030-17462-0_16.pdf), [Local PDF](./2019-voxlogica1-springer-paper.pdf)
- **Summary:**
  - Introduces VoxLogicA, a tool merging computational imaging (ITK) with declarative, logic-based spatial model checking.
  - Enables rapid, logic-based development for medical image analysis, especially brain tumor segmentation.
  - Achieves state-of-the-art accuracy, explainability, and replicability, with a two-orders-of-magnitude speedup over topochecker.
  - [Full extracted text available in `references/2019-voxlogica1-springer-paper.txt`]

### 2. VoxLogicA-2 Design (ISOLA 2024)

- **Title:** Towards Hybrid-AI in Imaging using VoxLogicA
- **Authors:** Vincenzo Ciancia, Laura Bussi, et al.
- **PDF:** [ISOLA2024 PDF](https://iris.cnr.it/bitstream/20.500.14243/517025/3/ISoLA24.pdf), [Local PDF](./2024-ISOLA24-voxlogica2-ciancia-bussi.pdf)
- **Summary:**
  - Presents the design and vision for VoxLogicA-2, focusing on hybrid-AI approaches in medical imaging.
  - Emphasizes modularity, explainability, and integration with modern AI/ML workflows.
  - Sets the foundation for future research and development in explainable, logic-based image analysis.
  - [Full extracted text available in `references/2024-ISOLA24-voxlogica2-ciancia-bussi.txt`]

---

## Current State & Goals

- **Implementation Language:** F# (modular, open to future multi-language evolution)
- **Core Modules:** Parsing, reduction, execution planning, and logic engine (see `src/`)
- **Design Principles:**
  - Modularity and flexibility for rapid scientific evolution
  - Clear separation of concerns and extensibility
  - Professional, readable, and well-documented codebase
- **Immediate Priorities:**
  - Finalize and document the reduction and execution engine
  - Expand onboarding and contributor documentation
  - Integrate and reference all foundational scientific work

---

## How to Use This Documentation

- Start here for a high-level overview and links to key resources.
- See the `references/` directory for full papers and extracted text.
- Explore module-specific docs (to be expanded) for technical deep-dives.
- Contribute improvements, corrections, and new knowledge as the project evolves.

---

_This documentation is maintained as a living resource. For questions or contributions, contact the project leads or open an issue in the repository._
