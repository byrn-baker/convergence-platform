# Browser qualification

Run on Linux with Node 24, OpenSSL, certutil (libnss3-tools) and a disposable PostgreSQL 16 database. The test creates and drops a random schema, launches both reviewer processes on loopback, signs in two Chromium contexts, saves an audited disposition and checks role, site, hostile-origin, native-route and logout behavior. It installs the temporary test CA into a temporary browser profile and never disables certificate validation.

Install the Convergence API dependencies with npm ci in ui/convergence-api. In this directory run npm ci and npx playwright install --with-deps chromium. Set REVIEWER_TEST_DATABASE_URL to the disposable PostgreSQL connection URL. Run ./run.sh. On a stacked checkout set REVIEWER_API_DIR to the ui/convergence-api directory from the service branch. REVIEWER_CHROMIUM and CERTUTIL can select existing compatible executables.
