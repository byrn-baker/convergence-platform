# Embedded Convergence sign-in for the HUD

**Status** Proposed for design review.

An operator viewing a case in a trusted local NetClaw HUD should be able to sign in to Convergence and record a disposition without changing applications. NetClaw identifies the HUD owner session and installation. Convergence identifies the user, permitted sites and role. A valid request requires both authorizations.

1. The workspace starts signed out and offers sign-in in the eligible local profile. A viewer can read cases for their authorized sites. The service denies an operator action attempted by a viewer even if the browser manually constructs the request.
2. An operator can record a disposition for a case in an authorized site. The service records the authenticated user and the selected site with the action. A case ID from another site must reveal no case contents. Browser-supplied actor or role fields have no authority.
3. Switching installation, signing out, expiry and revocation clear displayed private data and invalidate further operations. A late response from the previous session cannot populate the current workspace. A downgrade from operator to viewer takes effect at the next operation.
4. Eligibility for a view does not grant access to its service. The adapter denies foreign origins and ineligible host profiles. Serve standalone preview assets on a separate origin without a production owner cookie or adapter mount. The preview UI must make zero adapter requests. Existing standalone Convergence login and NetClaw's built-in retained-evidence views continue to work independently.

The initial write for qualification is a security disposition. Reinvestigation and JEV review enqueue external work and require separate retry and audit qualification before activation.
