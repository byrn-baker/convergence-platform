# Browser qualification

Run on Linux with Node 24, OpenSSL, certutil (libnss3-tools) and a disposable PostgreSQL 16 database. The test creates and drops a random schema, launches both reviewer processes on loopback, signs in two Chromium contexts, discards the response to a committed disposition, recovers its receipt after reloading and checks role, site, hostile-origin, native-route and logout behavior. It installs the temporary test CA into a temporary browser profile and never disables certificate validation.

Install the Convergence API dependencies with npm ci in ui/convergence-api. In this directory run npm ci and npx playwright install --with-deps chromium. Set REVIEWER_TEST_DATABASE_URL to the disposable PostgreSQL connection URL. Run ./run.sh. On a stacked checkout set REVIEWER_API_DIR to the ui/convergence-api directory from the service branch. REVIEWER_CHROMIUM and CERTUTIL can select existing compatible executables.

The reinvestigation scenario also starts `receiver_fixture.py` using
`REVIEWER_RECEIVER_PYTHON` (default `python3`); install
`services/alert-receiver/requirements.txt` in that Python environment. It uses real
HTTPS, receiver authentication, PostgreSQL outbox leases, SQLite admission and
queue workers. Case/device reads and the external investigation hook are synthetic.
A dropped browser response and a dropped receiver acknowledgement must still
produce one attributed note, one receiver job and one counted hook call. The
fixture's correlated synchronous result is not a claim that the deployed gateway
supports correlated completion.
