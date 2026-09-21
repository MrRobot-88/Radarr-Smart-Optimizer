# Radarr Smart Optimizer

> ## Smart Optimizer UI (easiest setup)
> Prefer one container with a web dashboard? Use [Smart Optimizer UI](https://github.com/MrRobot-88/Smart-Optimizer-UI). It bundles the Radarr and Sonarr optimizers and lets you configure connections from the browser.

Radarr Smart Optimizer revisits movies already in your Radarr library and searches for smaller replacement releases while applying conservative media-safety rules before asking Radarr to grab anything.

It is **dry-run by default**. The script never calls Radarr DELETE endpoints and never controls your download client directly. In live mode it sends a selected release to Radarr; Radarr then handles downloading, import and normal file replacement.

## Current behavior

- Persistent **A-Z movie queue** with a saved cursor between runs.
- Newly added movies are appended to the end of the existing optimizer queue.
- Optimizer-only movie exclusions can be stored in the shared control file; excluded movies are skipped before an interactive release search.
- Storage-first replacement policy: a replacement must remain inside the configured minimum/maximum saving window.
- No resolution downgrade.
- AV1 candidates are rejected.
- Dolby Vision-only candidates without HDR fallback are rejected.
- Existing HDR/Dolby Vision state is protected by the dynamic-range rules.
- Audio/channel and Atmos information is considered when ranking otherwise-valid candidates.
- Release ranking prefers dynamic range, Atmos, channel count and then smaller size/x265 among candidates that already passed the hard safety gates.
- Active Radarr queue items are skipped/rechecked before a live grab.
- Search history, queue position and attempted releases are persisted.
- At most two optimizer search cycles per movie, with a 180-day wait before the second cycle.
- Manual UI mode can use `SMART_OPTIMIZER_TARGET_GRABS`: the requested number represents successful releases sent to Radarr, while the normal search budget remains the ceiling.

**Radarr remains storage-first.** It does not use Sonarr's special low-resolution +50% size-growth rule.

## Requirements

- Radarr with its v3 API reachable from the machine running the script
- Python 3.8+
- Radarr API key
- A working indexer setup in Radarr
- A download client already configured in Radarr

No third-party Python packages are required.

Prowlarr works well for managing indexers, but the optimizer does **not** call the Prowlarr API and does not require a Prowlarr API key.

## Quick start

1. Download `radarr-smart-optimizer.py`.
2. Supply the API key through `RADARR_KEY` or a protected wrapper/key file. Do not put the key in the Python source.
3. Review the normal/UHD profile IDs used by the script.
4. Run a dry run:

```sh
export RADARR_KEY="$(cat /path/to/.radarr-smart-optimizer-key)"
python3 radarr-smart-optimizer.py
```

When the proposed replacements look correct:

```sh
python3 radarr-smart-optimizer.py --live
```

In Radarr, the API key is under **Settings → General → Security → API Key**.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `RADARR_URL` | `http://127.0.0.1:7878` | Radarr URL |
| `RADARR_KEY` | none | Radarr API key; required |
| `RADARR_SEARCHES_PER_RUN` | `10` | Maximum interactive searches per normal execution |
| `RADARR_OPTIMIZER_STATE` | state JSON beside script | Persistent optimizer state |
| `RADARR_MIN_SAVING_PERCENT` | `5` | Minimum required saving |
| `RADARR_MAX_SAVING_PERCENT` | `50` | Maximum allowed saving / quality-risk guardrail |
| `SMART_OPTIMIZER_CONTROL` | control JSON beside script | Optional shared runtime controls/exclusions |
| `SMART_OPTIMIZER_TARGET_GRABS` | `0` | Manual/UI target; 0 keeps normal search-count behavior |

The standalone script's base daily search budget is currently **300** searches. The shared control file can supply date-scoped temporary extra searches and override the min/max saving window.

The current script uses normal profile ID `4` and UHD profile ID `5`; verify those IDs against your own Radarr installation before live mode.

## Safety policy

Every candidate must pass the optimizer's hard checks before ranking. In particular, Radarr's optimizer is designed to reduce storage use rather than permit a larger file merely because it has a higher resolution.

Dynamic-range policy is conservative: an existing HDR file cannot be replaced by SDR/unknown, and an existing DV+HDR file requires DV+HDR. Dolby Vision without explicit HDR fallback is rejected. Candidate detection depends partly on release metadata and naming, so **dry-run against your own indexers before enabling live mode**.

## State and exclusions

By default the state file is `radarr-smart-optimizer-state.json` beside the script. It contains the persistent queue/cursor, search-cycle information, daily counters and attempted releases. Do not commit it.

When `SMART_OPTIMIZER_CONTROL` points at a control JSON used by Smart Optimizer UI, the Radarr section can also provide downsize controls, temporary daily allowance and optimizer exclusions. Excluding a movie only tells the optimizer not to search/replace it; it does **not** delete the movie or its files.

## Scheduling

After validating dry-run output, schedule the live command with cron, Synology Task Scheduler or another scheduler. Keep the API key in a protected environment/wrapper rather than in the task text or repository.

Example wrapper:

```sh
#!/bin/sh
export RADARR_KEY="$(cat /path/to/.radarr-smart-optimizer-key)"
exec python3 /path/to/radarr-smart-optimizer.py --live
```

Protect the files:

```sh
chmod 600 /path/to/.radarr-smart-optimizer-key
chmod 700 /path/to/run-radarr-smart-optimizer.sh
```

## Related projects

- [Smart Optimizer UI](https://github.com/MrRobot-88/Smart-Optimizer-UI)
- [Sonarr Smart Optimizer](https://github.com/MrRobot-88/Sonarr-Smart-Optimizer)
- [Deluge Smart Cleanup](https://github.com/MrRobot-88/Deluge-Smart-Cleanup)
