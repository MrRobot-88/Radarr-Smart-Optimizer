# Radarr Smart Optimizer v2.0 staging

The standalone Radarr optimizer source on this branch is currently the same tested engine as the existing main branch.

The post-v1.1 Radarr hardening completed on the live system is primarily in the combined Smart Optimizer UI worker, including guarded native-import reconciliation and duplicate/old-file recovery. The exact newest live UI source still needs to be synced before the combined v2.0.0 release is finalized.

This staging branch exists so Radarr, Sonarr and the combined UI have matching v2 work branches without publishing an incomplete release.

No API keys, connection files, optimizer state, or control JSON are included.
