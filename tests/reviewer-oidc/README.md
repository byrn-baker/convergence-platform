# Cases sign-in against a disposable Keycloak

Qualifies spec 1018 C2–C4 end to end: the real reviewer HUD, the real reviewer service
over mutual TLS, a PostgreSQL fixture and a throwaway Keycloak at the version that
production pins.

Run `npm ci` in `ui/convergence-api`, then:

```
REVIEWER_TEST_DATABASE_URL=postgres://… tests/reviewer-oidc/run.sh
```

`run.sh` starts Keycloak in development mode on loopback, creates a realm with fixture
users, runs `keycloak.mjs` and removes the container. Keycloak serves plain HTTP there, so
the test passes `allowInsecureLoopback`; no deployment setting enables that.

The unit suites cover the cases that need a forged token or a controlled clock
(`ui/convergence-api/test-reviewer-oidc.cjs`, `ui/convergence-hud/test/reviewer-oidc.mjs`).
This suite covers the same list with tokens that Keycloak really issued.
