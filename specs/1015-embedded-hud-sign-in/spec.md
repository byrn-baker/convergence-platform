# Embedded Convergence sign-in for the HUD

**Status** Proposed for design review.

An operator viewing a case in a trusted local NetClaw HUD or its dedicated HTTPS reviewer entry point should be able to sign in to Convergence and record a disposition without changing applications. For local owners NetClaw checks the owner session and installation. On the dedicated HTTPS reviewer origin the HUD authenticates its installation to Convergence and binds a separate browser session to the reviewer after sign-in. Convergence verifies the user, permitted sites and role on both paths.

1. The workspace starts signed out and offers sign-in in the eligible local or dedicated HTTPS reviewer profile. A viewer can read cases for their authorized sites. The service denies an operator action attempted by a viewer even if the browser manually constructs the request.
2. An operator can record a disposition for a case in an authorized site. The service records the authenticated user and the selected site with the action. A case ID from another site must reveal no case contents. Browser-supplied actor or role fields have no authority.
3. Switching installation, signing out, expiry and revocation clear displayed private data and invalidate further operations. A late response from the previous session cannot populate the current workspace. A downgrade from operator to viewer takes effect at the next operation.
4. Eligibility for a view does not grant access to its service. The adapter denies foreign origins and ineligible host profiles. Serve standalone preview assets on a separate origin without a production owner cookie or adapter mount. The preview UI must make zero adapter requests. Existing standalone Convergence login and NetClaw's built-in retained-evidence views continue to work independently.

Two people on separate workstations can review the same investigation using their own accounts. A disposition records the acting user. The dedicated network origin exposes only the review view and its scoped operations. Reviewer sessions cannot reach local HUD owner issuance, consoles, chat, model settings or any general-purpose proxy. Claiming, assignment and handoff are separate case-management features.

The initial write for qualification is a security disposition. Reinvestigation and JEV review enqueue external work and require separate retry and audit qualification before activation.

## Approved next increment: attributed reinvestigation

Implement the reinvestigation command under [dispatch-contract.md](dispatch-contract.md), including durable acceptance, relay, receiver deduplication, explicit ambiguous outcomes and browser recovery. Keep JEV disabled. Preserve reviewer authentication boundaries and existing machine/manual callers.
