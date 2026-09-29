---
id: public-probe
condition: draft
revision: 1
depth: tiny
priority: 100
created_at: 2000-01-01T00:00:00+00:00
approved_by: null
approved_at: null
approval_ref: null
---

# Synthetic public assertion

## Intent

Provide a harmless one-node fixture for public preflight and inspection tests.

## Context and Evidence

New synthetic data. The fixed creation timestamp is test data, not an observed event. No human approval or prior execution is represented.

## Requirements

REQ-001: The local Python assertion evaluates two plus two as four.

## Constraints

CON-001: Never hand-edit state.yaml. The primary appends observed proof to core-created evidence.md.

## Non-goals

NG-001: This fixture grants no live execution, review or publication authority.

## Assumptions

A human-provided Python interpreter is available for later synthetic test execution.

## Proof obligations

VER-001: An owned synthetic test executes the local assertion, preserves the original draft pair, and labels its results as test observations rather than human approval.

## Approval

Unapproved synthetic draft. No real human decision is recorded.
