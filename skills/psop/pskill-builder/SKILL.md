---
name: pskill-builder
description: Build PSkill drafts from materials, domain knowledge, and expert guidance.
allowed-tools:
  - psop.pskills.get
  - psop.materials.list
  - psop.materials.read_analysis
  - psop.repository.read_file
  - psop.repository.propose_patch
  - psop.pskill_manifest.parse
  - psop.pskill_manifest.render
  - psop.memory.search
  - psop.memory.write_candidate
---

# PSkill Builder

Builds draft PSkill source patches from analyzed materials and human requirements.
