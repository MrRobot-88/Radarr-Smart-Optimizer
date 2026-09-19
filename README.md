# Radarr Smart Optimizer

A small Python tool that searches your **existing Radarr library** for smaller replacement releases while applying safety checks before it asks Radarr to grab anything.

It is **dry-run by default**. It does not delete media files, call Radarr DELETE endpoints, or control your download client directly. In live mode, the script sends the selected release to Radarr and lets Radarr handle its normal download/import/replacement workflow.

## Why use it?

Over time a Radarr library can end up with movie releases that are much larger than necessary. This optimizer revisits existing movie files and looks for smaller alternatives **without intentionally trading away the media properties it is designed to protect**.

It is aimed at people who want to reduce storage use without simply lowering every quality profile or manually searching a large movie library.

## What it protects

- Keeps 1080p at 1080p and 2160p at 2160p in the current optimizer logic.
- Requires a minimum size saving for same-resolution replacements.
- Protects HDR and Dolby Vision compatibility rules.
- Rejects Dolby Vision-only candidates where an HDR fallback is required.
- Protects audio channel count and Atmos where detected.
- Rejects releases with no known seeders.
- Blocks suspicious executable/script filenames in release titles.
- Rechecks the Radarr queue immediately before a live grab.
- Remembers search attempts in a local state file.
- Runs at most two optimizer search cycles per movie, with a 180-day wait before the second cycle.

## Prowlarr / indexers

This project was developed and tested with **Prowlarr** managing the indexers used by Radarr.

For the setup documented here, **Prowlarr is recommended and expected**: configure your indexers in Prowlarr and sync them to Radarr before running the optimizer.

The optimizer itself does **not** connect to the Prowlarr API and does not need a Prowlarr API key. It asks Radarr for available releases through Radarr's normal API, so Radarr continues to use the indexers supplied by Prowlarr and keeps its normal rejection rules in control.

## Indexers and Prowlarr

This project was developed and tested with **Prowlarr** managing the indexers used by Sonarr/Radarr. **Prowlarr is not required.** The optimizer does not communicate with Prowlarr directly; it asks Sonarr/Radarr for releases through their normal API, so you can use Prowlarr or another indexer setup supported by Sonarr/Radarr.

## Requirements

- Radarr with its v3 API reachable from the machine running the script.
- Python 3.8+.
- Your Radarr API key.
- Prowlarr configured with your indexers and synced to Radarr.
- A download client already configured normally in Radarr.

No third-party Python packages are required.

## Quick start

This is designed to be **download, edit, run**.

1. Download `radarr-smart-optimizer.py`.
2. Open it in any text editor.
3. Near the top, paste your Radarr API key into `RADARR_API_KEY`.
4. Set `SEARCHES_PER_RUN` to the maximum number of interactive searches you want each run (default: `50`).
5. **Review `NORMAL_PROFILE_ID` and `UHD_PROFILE_ID`** and make sure they match your Radarr quality-profile IDs.
6. Save the file and run:

```sh
python3 radarr-smart-optimizer.py
```

That is a **dry run**. It will show what it would choose without starting downloads or changing persistent optimizer state.

When the dry-run results look right:

```sh
python3 radarr-smart-optimizer.py --live
```

Live mode can ask Radarr to grab releases.

### Where to find the API key

In Radarr, open **Settings → General → Security → API Key**. Copy that value into `RADARR_API_KEY` near the top of the script.

You do **not** need to edit the Python code anywhere else for a normal setup.

### Optional: environment variables

If you prefer not to put the API key in the script, environment variables still override the quick-setup values:

```sh
export RADARR_KEY='YOUR_API_KEY'
export RADARR_SEARCHES_PER_RUN=50
python3 radarr-smart-optimizer.py
```

This is useful for Docker, cron and Synology Task Scheduler.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `RADARR_URL` | `http://127.0.0.1:7878` | Radarr URL |
| `RADARR_KEY` | none | Radarr API key (required) |
| `RADARR_SEARCHES_PER_RUN` | `50` | Maximum interactive searches per execution |
| `RADARR_OPTIMIZER_STATE` | state JSON beside the script | State-file location |
| `RADARR_DAILY_SEARCH_BUDGET` | `300` | Maximum optimizer searches per live day |
| `RADARR_MIN_SEEDERS` | `1` | Minimum known seeders |
| `RADARR_MIN_SAVING_PERCENT` | `5` | Minimum same-resolution saving |
| `RADARR_NORMAL_PROFILE_ID` | `4` | Normal profile ID used by this version |
| `RADARR_UHD_PROFILE_ID` | `5` | UHD profile ID used by this version |

Radarr normally uses port **7878**. Set `RADARR_URL` if yours uses another port.

## 4K Dolby Vision + HDR

The current optimizer includes its conservative 2160p Dolby Vision + HDR size policy. Dolby Vision-only candidates are rejected; releases classified as Dolby Vision + HDR fallback are handled separately by the optimizer's safety rules.

Review a dry run against your own release naming/indexers before live mode because HDR/DV detection depends partly on release metadata.

## Scheduling

After testing manually, schedule the same command with cron, Synology Task Scheduler, or another scheduler. Keep the API key in an environment variable or protected wrapper/key file instead of committing it.

The script enforces its configured daily search budget even if it is scheduled several times per day.

### Synology DSM Task Scheduler example

On Synology DSM, open **Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script**.

Use a user that can run Python and access the optimizer folder. Under **Schedule**, choose how often you want it to run. A practical example is every 4 hours.

Under **Task Settings → User-defined script**, use a protected wrapper script rather than putting the API key directly in Task Scheduler. Example wrapper:

```sh
#!/bin/sh
export RADARR_KEY="$(cat /path/to/.radarr-smart-optimizer-key)"
exec python3 /path/to/radarr-smart-optimizer.py --live
```

Protect the key and wrapper:

```sh
chmod 600 /path/to/.radarr-smart-optimizer-key
chmod 700 /path/to/run-radarr-smart-optimizer.sh
```

Then make the DSM task run:

```sh
/path/to/run-radarr-smart-optimizer.sh
```

Run the optimizer manually in **dry-run mode first**. Only add `--live` to the scheduled wrapper after you have checked its proposed replacements.

## State file

By default the optimizer creates `radarr-smart-optimizer-state.json` beside the script. It tracks optimizer search cycles, daily search counts, and recently attempted releases. Do not commit this file.

Existing old state entries without `search_cycles` do not automatically count as one of the new two-cycle searches.

## Important

Release metadata can be incomplete or misleading. **Dry-run first** and check what the optimizer proposes for your own library before enabling `--live`.

Looking for TV episodes instead? See **Sonarr Smart Optimizer**: https://github.com/MrRobot-88/Sonarr-Smart-Optimizer


## Related projects

- https://github.com/MrRobot-88/Sonarr-Smart-Optimizer
- https://github.com/MrRobot-88/Radarr-Smart-Optimizer
- https://github.com/MrRobot-88/Deluge-Smart-Cleanup

