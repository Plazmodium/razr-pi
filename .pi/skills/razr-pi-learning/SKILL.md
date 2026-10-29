---
name: razr-pi-learning
description: Learn Pi while exploring a minimal Razr workflow port through small verified labs.
---

# Razr–Pi learning guide

Use for beginner onboarding, harness concepts, discovery, and port decisions in this workspace. This is not the full Razr delivery skill.

1. Locate the workspace from this file (three parents above its directory) and read README.md and WORKSHOP.md. Resolve paths rather than relying on a fixed machine location.
2. Read docs/01-first-session.md for onboarding; docs/02-port-map.md for mappings; docs/03-learning-labs.md for experiments. For feature decomposition, read docs/05-vertical-slices.md and use templates/vertical-slice.md: plan one thin end-to-end outcome per slice, show explicit blockers and proof, and avoid layer-only delivery issues. First establish the tracer bullet; dispatch later slices only when prerequisites and shared-interface compatibility are verified.
3. Explain one relevant concept in plain language, propose one bounded action, and inspect its actual result. Avoid a long lecture or unseen autonomous setup.
4. Discover retrievable facts with tools. Ask the human only material choices, recommend a default, and distinguish recommendations from approved decisions.
5. Keep nontrivial exploration lean: intent, alternatives, constraints, proof ideas, open questions. Do not equate enthusiasm with exact spec approval.
6. Preserve the original Razr gate semantics. Never imitate state transitions by editing JSON/YAML manually; do not invoke an installer intended for another harness to configure Pi.
7. Record settled project decisions and unresolved blockers in WORKSHOP.md only when useful; no chat dumps, credentials, invented output or fake critic provenance.
8. Stop at each learning lab's observable exit. Report what was verified and what remains untested.

Do not install Pi, mutate globals, configure providers/MCP, create remotes, push, merge or deploy unless specifically authorized. For fresh critics use separate real sessions with explicit candidate inputs; model switches and builder-history forks do not count.
