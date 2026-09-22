# Radarr Smart Optimizer v1.1.0

## Highlights

- Added targeted movie execution with `SMART_OPTIMIZER_MOVIE_ID`.
- Added explicit `SMART_OPTIMIZER_MANUAL_TARGET` mode for UI-triggered targeted runs.
- Targeted Manual Optimizer runs do not consume the normal persistent A-Z queue or daily-search counter.
- Current movie files below **5 GiB** are skipped before interactive searching.
- Added optimizer download ownership binding.
- Added guarded source-tier-only import recovery for optimizer-owned downloads without globally weakening Radarr quality profiles.
- Replacement completion is verified using Radarr's actual registered movie-file state.
- Old-file cleanup is duplicate-safe and only targets the exact recorded old file after replacement verification.
- Preserved storage-first saving rules and HDR/DV, AV1, audio/channel and Atmos-aware protections.
