# Attributed requests for a shared JEV assessment

Approved by the user on 2026-11-15. Implementation and isolated qualification
precede any production activation or deliberate provider call.

Each operator/admin request has an immutable installation, actor, operation UUID,
site, case and command identity. Its receipt records whether it started, joined
or reused the shared assessment. Replaying the same identity returns that receipt;
changing case, site or command conflicts after current authorization checks.
Viewing a receipt requires the same authenticated installation/actor and current
site access. Viewer accounts cannot submit.

The canonical assessment belongs to the site/case, independently of its callers.
It uses the existing `case_task(site, case)` identity, JEV ledger, daily/per-case
budgets, allowlisted IDS abstraction and receiver review lane. Legacy and reviewer
requests serialize through the same admission seam. A second request never means
another paid evaluation. Reevaluation is outside this increment.

Assessment state and delivery state are distinct. A correlated persisted result
can report success, budget exhaustion, disabled provider, unavailable evidence,
provider failure or unknown outcome. It is not a Gateway completion and does not
change case disposition or append investigation feedback. Persist the canonical
result before publication; publication retry cannot cause reevaluation. A durable
request-to-assessment association survives ordinary job cleanup and restart.

The existing reviewer outbox/relay boundary carries a separately enabled JEV
command. Shared delivery primitives own leases, authentication and retry; the JEV
adapter owns its envelope, receipt projection and completion checks. Do not
extend the investigation Gateway success predicate to accept an assessment.

Qualification must include two simultaneous reviewers and a legacy caller sharing
one provider attempt; same-operation replay and conflicting inputs; lost ACK,
lease expiry and restart at each checkpoint; daily/per-case budget exhaustion;
publication failure and immutable result recovery; revoked/cross-site/viewer
denials; CSRF and late browser response suppression. Use a counted synthetic
provider for integration. Live commissioning is a separate bounded step.
