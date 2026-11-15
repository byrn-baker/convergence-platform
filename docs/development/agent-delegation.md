# Grok Build delegation

The owner requests use of the existing Grok Build subscription to offload suitable
engineering work. Prefer it for bounded documentation, source inventory, test
drafting, mechanical fixes and secondary review with deterministic acceptance.
Start with a small qualified task; expand responsibility based on checked results.

1. Check the installed `grok --help` and authenticated subscription path. Use the
   existing login; keep API-key fallback unset for subscription tasks. If login,
   quota or the selected model is unavailable, report the limitation and continue
   with an available agent. Do not silently switch to paid API usage.
2. Provide one task, sanitized inputs, an isolated working directory, exact file
   ownership, acceptance checks, deadline/turn limit and reporting destination.
   The current coordinator's tighter iteration/concurrency budget takes priority
   over generic agent-template defaults.
3. For supplied-text review, use read-only permissions, no subagents, disabled web
   access and denied tools. For implementation, grant only the assigned checkout
   and required tools; inspect the installed CLI's sandbox/tool semantics first.
   CLI permissions are not a substitute for isolation or output review.
4. Keep credentials, private backups and production case contents out of prompts.
   Grok does not own live deployment, destructive recovery, credential changes,
   architectural decisions or approval. It may review sanitized plans for these.
5. Inspect every changed file and rerun relevant checks independently. Record the
   task, actual output, validation and gaps. Model confidence is not acceptance.

The coordinator should select a suitable offload when one exists, rather than
leaving this subscription unused by default. A plan review cannot certify a
runtime installation or recovery that the reviewer has not exercised.
