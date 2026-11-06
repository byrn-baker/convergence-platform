# Deployment bundle

A bundle has schema version, deployment kind, absolute source root, site,
context/namespace/release (K3s) or project/ports (Docker), explicit storage/image
choices, generated file inventory and command arrays. Private credentials are
separate from nonsecret Helm values. A completed manifest is written last.

States: absent → prepared → installed → verified. Preparation never implies
installation; readiness never implies real device visibility. An incomplete bundle
or identity change is a conflict requiring recovery rather than credential rotation.
