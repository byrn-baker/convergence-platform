# Spec 1004: Investigation Feedback Loop

**Status**: Complete (Phase 6 shipped)  
**Mission**: Operator verdicts on investigations improve future triage via RAG  
**Extracted from**: `080-convergence` US7, Phase 6 (T060–T063)

## What this is

The human-in-the-loop closure: operators review escalated investigations in the
HOME Triage view, submit feedback (correct / wrong / need more), and trigger
reinvestigation. Feedback connects to the RAG pipeline so future investigations
on similar alerts perform better.

## Scope (in)

- Triage sub-view in HUD HOME (escalated list, notes, feedback buttons)
- "Need More" → reinvestigate API call
- RAG document ID display for traceability
- Skill wording updates for multi-vendor / adapter language
- convergence-api event feedback endpoints

## Scope (out)

- The RAG storage infrastructure itself (that's the rag-mcp spec)
- The HUD shell / tab routing (that's 1005)
- Investigation decision logic (that's 1001)

## Key files

| Path | Role |
|------|------|
| `ui/netclaw-visual/src/views/home/` | Triage sub-view |
| `ui/convergence-api/` | Event + feedback API routes |
| `workspace/skills/alert-triage` | Investigation skill |
| `workspace/skills/wifi-diagnosis` | Wi-Fi skill (multi-vendor wording) |

## Functional requirements (from 080)

FR-009 (alert path including RAG)

## Success criteria

- SC-004: Synthetic WifiDegraded produces a diary event visible in HOME within SLA

## Tasks (all complete)

T060–T063 (Phase 6).
