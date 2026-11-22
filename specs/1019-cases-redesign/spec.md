# Cases redesign: a case-first threat-hunting site

**Status** New feature, exempted from the spec 1017 feature freeze by the owner on
2026-11-22 (AGENTS.md; spec 1017 A03). Each live step still needs owner approval.

## Problem

After sign-in the Cases site shows an empty workspace. It can open a case only by its ID,
so a reviewer needs a link or a pasted UUID to do anything. There is no list, search or
queue, and the page puts session controls, telemetry, disposition, assessment and
investigation forms on one long screen. Site `home` holds about 703,000 intrusion cases
(2026-11-22), almost all with no disposition, and the site offers no way to find the ones
worth a person's time.

## Required behavior

1. The landing page after sign-in is a case queue for the reviewer's site. It is never
   empty when the site has cases.
2. The queue groups cases by detection signature, with a count, the highest severity, the
   last time seen and how many still need review. A reviewer opens a signature to see its
   cases, newest first, and opens a case from there.
3. A reviewer can filter by time window, severity, signature and review state (needs
   review, reviewed, each outcome), and can still open a case directly by ID or link.
   Existing `#case=<id>&site=<site>` links keep working.
4. A case page shows what a reviewer needs to decide, in this order: what fired, when and
   between which hosts; the evidence and address context; the current disposition and its
   history; then the disposition form. Assessment and investigation requests stay
   available but are secondary.
5. A reviewer can move through the queue and a signature's cases with the keyboard, and
   go to the next case after saving a disposition without returning to the list.
6. The site has an original threat-hunting look in the style of 1990s cyberpunk film:
   dark, neon accents, monospace type, wireframe and grid motifs. It reuses no film's
   names, logos, dialogue or artwork.
7. The look does not cost usability. Text meets WCAG AA contrast, nothing flashes, glow
   and scanline effects stop under `prefers-reduced-motion`, and the layout works on a
   phone.
8. Authorization does not change. A reviewer sees only cases for sites their account
   holds, every list and count is scoped on the service, and every disposition keeps the
   person's name. The queue and lists answer within one second at production size.

## Out of scope

- Disposition of many cases in one action. The audit model records one attributed
  decision per case. Bulk review needs its own design and is an open question for the
  owner (plan.md).
- Changes to accounts, roles, sign-in, IDS ingestion or alert routing.
- The NetClaw HUD. This is the Cases site only.
- Other alert kinds. The queue lists intrusion cases; other events stay readable by ID.
