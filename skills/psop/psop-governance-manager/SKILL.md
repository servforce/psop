---
name: psop-governance-manager
description: Convert findings into reviewable and rollbackable PSOP governance proposals.
allowed-tools:
  - psop.evaluations.read
  - psop.governance.write_proposal
  - psop.agent_version.activate
  - psop.skill_version.activate
---

# PSOP Governance Manager

Creates governance proposals without directly activating production changes.
