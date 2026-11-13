# Convergence HUD views

Package the existing Convergence views and their synthetic UI tests in convergence-platform. Preserve the operator's Host Heartbeat and VM Security changes and the historical classic and React browser entry points. The shared NetClaw dashboard extension point is proposed separately. This package ships source for staging. Enabling it on a production HUD requires qualification of the host integration and its access controls.

The staging integration builds a separate NetClaw HUD artifact from an upstream commit. Classic and React consume the platform view source, retain native retained-flow access and use the native SSH terminal. The ordinary HUD can dispatch approved reads for an authorized site. Case changes go through the dedicated per-user HTTPS reviewer. A failed upgrade restores the prior installation, including its saved configuration.
