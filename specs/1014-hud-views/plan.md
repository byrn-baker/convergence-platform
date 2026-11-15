# Plan

Place the module's browser source under `ui/convergence-hud/modules/convergence` and keep the original tests beside it under `test`. The dashboard and classic exports have separate host contracts. The package accepts the host's React instance as a peer dependency. jsdom is required only to run the synthetic tests.

Keep the original module-relative imports, styles, manifest, geographic attribution and license. Preserve the standalone Proxmox renderer with its current wiring. The server proxy in the fork accepted broad requests with a shared service token. An authenticated installation- and site-bound adapter needs its own integration review before enabling the views.

Compare extracted files with the preserved source. Run the tests from a clean npm installation and include them in the platform Node CI job. Verify VM heartbeat rendering and site isolation with synthetic fixtures. Host extension development, browser qualification, service installation and migration are separate deliverables.
