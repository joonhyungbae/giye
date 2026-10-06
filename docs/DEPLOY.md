# Deploying giye.org

The public site runs on a small Vultr VPS in Seoul behind Cloudflare. The home machine keeps the ledger, the evidence and the pipeline. The VPS receives only the built server (`.output/`) and the published snapshot (`data/site/`).

```
visitor ──HTTPS──> Cloudflare edge ──Tunnel──> cloudflared (VPS) ──> node 127.0.0.1:3000
                   (TLS, www redirect,                               giye-web.service
                    burst limit, Bot Fight Mode)                     reads /srv/giye/data/site
home machine ──rsync over SSH──> /srv/giye/.output, /srv/giye/data/site
             <──deploy/pull_requests.sh── /srv/giye/data/work/requests.jsonl
```

Why this shape:

- **Person-level data is not released as a dataset.** The VPS holds what the pages show and nothing more. The ledger (`data/ledger/`), original evidence and research data never leave the home machine.
- **No web port is open on the VPS.** cloudflared dials out to Cloudflare, so every visitor passes the edge. The VPS firewall allows SSH from the home address only.
- **The scrape guard counts visitors by `cf-connecting-ip`.** `GIYE_TRUSTED_IP_HEADER` in `deploy/giye-web.service` names that header. Cloudflare sets it, and no other route reaches the port, so a client cannot forge it. Without the variable (local dev) the guard applies only its user-agent rule.
- **Self-reports are personal data.** `/request` appends to `data/work/requests.jsonl` on the VPS. `deploy/pull_requests.sh` moves them home and deletes them on the server.
- **Static hosting does not fit.** GitHub Pages, Cloudflare Pages and a spreadsheet back end would all have to ship the full person data to the browser (a bulk export), and they have no place for `/request` or the scrape guard.

## Credentials

`.env` at the repository root, mode 600, git-ignored:

```
VULTR_API_KEY=...
CLOUDFLARE_API_TOKEN=...
# CLOUDFLARE_ACCOUNT_ID=...   optional; taken from the token's zones when absent
```

- Vultr: Account → API → enable, and restrict access to the home IPv4.
- Cloudflare: My Profile → API Tokens → custom token with
  Zone · Zone · Edit, Zone · DNS · Edit, Zone · Zone Settings · Edit, Zone · Zone WAF · Edit,
  Zone · Bot Management · Edit, Zone · Single Redirect · Edit, Account · Cloudflare Tunnel · Edit; zone resources = all zones in the account.

The scripts write `~/.config/giye/host` (VPS address) and `~/.config/giye/tunnel-token`.

## First setup

```bash
deploy/vultr.sh        # Seoul instance (≥2 GB RAM), SSH key, firewall group: SSH from this machine only
deploy/cloudflare.sh   # zone, tunnel, DNS, HTTPS, www redirect, edge rate limit, Bot Fight Mode
deploy/provision.sh    # updates, Node 22, user giye, giye-web.service, cloudflared with the tunnel token
deploy/push.sh         # type check, build, copy .output and data/site, restart, local health check
```

`deploy/cloudflare.sh` prints two name servers. Set them for giye.org at Gabia (My page → domain → name server settings), replacing `ns.gabia.*`. The zone becomes active once the change propagates (minutes to a few hours). `https://giye.org` answers after that.

## Updating

| Change | Command |
|---|---|
| Code | `deploy/push.sh` |
| Data only | `deploy/push.sh --rebuild --data-only`: rebuilds the snapshot with the package (`giye normalize`, `giye publish`, `giye explore`, each with `--config deploy/giye.production.toml`), then copies `data/site` (the server re-reads changed files without a restart). Without `--rebuild` the snapshot already in `data/site` is pushed as it is. |
| Self-reports | `deploy/pull_requests.sh` (also run daily at 06:10 by the home machine's crontab, log in `data/work/logs/pull_requests.log`), then the maintainer imports them into the ledger (a private step; see Schedule) |

The site rebuild is these three package stages and nothing else:

```bash
giye normalize --config deploy/giye.production.toml   # data/processed; never edits the ledger
giye publish   --config deploy/giye.production.toml   # data/site, the snapshot the server reads
giye explore   --config deploy/giye.production.toml   # rim order of the home page
```

## Schedule

The home machine runs the weekly pipeline and publishes the snapshot (`crontab -e`). `$GIYE_HOME` is that checkout, and on the VPS it is the service account's home directory:

```
10 6 * * *  cd $GIYE_HOME && deploy/pull_requests.sh >> data/work/logs/pull_requests.log 2>&1
0 4 * * 1   $GIYE_HOME/deploy/scheduled.sh weekly
```

`deploy/scheduled.sh` backs up the ledger and runs the maintainer's weekly pipeline script, which is kept in the private repository because the roster collectors it drives name real programmes. That script runs the package stages with `deploy/giye.production.toml`: `giye collect` for programmes still publishing, `giye extract` (CV pull plus cache replay, with no unattended model call), `giye resolve`, and the site rebuild above (`giye normalize`, `giye publish`, `giye explore`). It also runs three maintenance steps as private scripts: the weekly link check and the import of self-reports, for which the package has no command, and evidence capture for cited pages. The package's evidence command is `giye evidence --config deploy/giye.production.toml` (a copy of every URL the ledger cites, through the same robots-checking fetcher); the reference archive still runs its older private script because that script also reads cited places the package does not (reviewer notes, search leads, hand transcriptions), and the two have not been compared on the real archive. A statement that the weekly run checks cited links describes the reference archive's pipeline, not something `giye` does. When the run succeeds, `deploy/scheduled.sh` pushes `data/site` with `deploy/push.sh --data-only`. After that it commits the ledger to the private repository (when `./gitp` exists) and copies the data that cannot be rebuilt to the VPS (`deploy/backup.sh`). A lock file keeps two runs from writing the ledger at once; a run that finds the lock taken is skipped and logged. Logs are in `data/work/logs/`.

New editions are not polled. The maintainer works in the field and collects a new edition's roster when it is published (`giye collect` with that programme's collector, then `giye resolve`), then runs `deploy/push.sh --rebuild --data-only`. Rosters of ended editions are collected once and not again.

## Operating notes

- SSH from a new address: rerun `SSH_FROM=<ip>/32 deploy/vultr.sh`, or use the Vultr web console.
- The scrape guard refuses HeadlessChrome and HTTP libraries by design. Check the site with a normal browser. `deploy/push.sh` checks the origin over SSH with a browser-like user agent.
- The VPS can be rebuilt from scratch with the four setup commands. It holds nothing that is not on the home machine, except self-reports not yet pulled.
- Backup: `deploy/backup.sh` copies `data/ledger`, `data/raw` (original bytes of cited pages), `data/reference` and the audit, CV-extraction, CV-diff and quarantine folders of `data/work` to `$GIYE_HOME/backup/` on the VPS (mode 700, outside `/srv/giye`, never served). It never deletes on the VPS side. Derived folders are rebuilt by the pipeline and are not copied. Restore with `rsync -az giye@<host>:backup/data/ data/`. Rebuilding the VPS deletes this copy, so run `deploy/backup.sh` again afterwards.
- Logs: `ssh giye@$(cat ~/.config/giye/host) journalctl -u giye-web -n 100` (the `giye` user may also run `sudo systemctl restart giye-web`).

## Contact

Person-level data requests and other mail go to [jh.bae@kaist.ac.kr](mailto:jh.bae@kaist.ac.kr). The same address is on the site footer, the `/data` page and the `/request` page.

## What the site sends

W1. A response that covers many people carries only coded structure: ids, years, type codes, and indexes into small name tables of programmes, venues and source domains (institutions and websites, not persons), plus person names. Free text of a record (title) and its full source URL are served one person at a time (`getArtistRecord`), where the scrape guard's per-visitor limit applies. A list endpoint sends only the fields its page reads.

Reason: the overview views need the structure of every record to draw, but the citable record (what, where, which source) is the dataset; serving it per person makes bulk copying cost one counted request per person.
