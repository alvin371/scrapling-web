# Deployment (Docker Swarm, built on the server via SSH)

This service deploys to the server as a **Docker Swarm stack** named `scrapling-web`,
matching the convention used by the other apps on the box (`forbes`, `sec-forbes`,
`mysql-8`, `dozzle`). **No container registry is used** — images are built directly on
the server (a single-node Swarm can run locally-built images), and deployment happens
over plain **SSH**.

```
push develop/main ─▶ GitHub Actions ─ssh▶ rsync repo + .env to server
                                      └─▶ deploy/deploy.sh (on server):
                                            build api+worker images
                                            docker stack deploy --resolve-image never
                                            db bootstrap + health check
```

## Topology on the server

| Service      | Image (built on server)      | Port (host)         | Notes                                   |
|--------------|------------------------------|---------------------|-----------------------------------------|
| postgres     | `postgres:15-alpine`         | internal            | **dedicated** DB for this app           |
| redis        | `redis:7-alpine`             | internal            | append-only persistence                 |
| api          | `scrapling-web-api`          | `18000` → nginx `scrap.acnenosystem.com` |
| worker       | `scrapling-web-worker`       | internal            | Scrapling + headless Chromium           |
| rq-dashboard | `eoranged/rq-dashboard`      | `19181`             | **unauthenticated** — front with nginx auth before exposing |

- The server has **only MySQL** (used by the PHP apps); this app is PostgreSQL-only,
  so it ships its **own** Postgres. They coexist.
- Persistence uses host bind-mounts under `/home/forbes/serve/scrapling-web/`
  (`postgres-data/`, `redis-data/`, `logs/`). The synced source lives in `repo/`.
- App secrets live in `/home/forbes/artifact/scrapling-web/.env`, rendered by CI from
  GitHub secrets and bind-mounted into the api/worker containers (Swarm ignores
  `env_file:`, so the file is mounted, not injected). `deploy.sh` also sources it to
  resolve the Postgres credentials for the stack file.

## One-time setup: deploy SSH key

CI authenticates to the server with a dedicated key you generate yourself (this
replaces the old Ansible key you no longer have):

```bash
# 1. generate a keypair (no passphrase)
ssh-keygen -t ed25519 -f scrapling_deploy -C "scrapling-web-ci" -N ""

# 2. add the PUBLIC key to the server (you already have SSH access as forbes)
ssh-copy-id -i scrapling_deploy.pub forbes@217.217.253.76
#   or append scrapling_deploy.pub to ~forbes/.ssh/authorized_keys manually

# 3. put the PRIVATE key contents into the GitHub secret SSH_PRIVATE_KEY
cat scrapling_deploy    # copy into the secret, then delete the local files
```

## Required GitHub Actions secrets

Add under **Settings → Secrets and variables → Actions**. Infra:

| Secret            | Value                                                        |
|-------------------|--------------------------------------------------------------|
| `SSH_PRIVATE_KEY` | private half of the deploy key created above                 |
| `SERVER_HOST`     | `217.217.253.76`                                             |

App / DB secrets (rendered into the server `.env`):

| Secret                 | Notes                                                      |
|------------------------|------------------------------------------------------------|
| `POSTGRES_USER`        | e.g. `scraper`                                             |
| `POSTGRES_PASSWORD`    | strong password (dedicated Postgres)                       |
| `POSTGRES_DB`          | e.g. `social_scraper`                                      |
| `SECRET_KEY`           | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `API_KEYS`             | comma-separated client keys                                |
| `INSTAGRAM_SESSION_ID` | Instagram `sessionid` cookie                               |
| `CORS_ORIGINS`         | optional — e.g. `https://scrap.acnenosystem.com` (empty ⇒ `*`) |
| `THREADS_SESSION_ID`   | optional                                                   |
| `SCRAPLING_PROXY`      | optional                                                   |

> No Docker Hub / registry credentials are needed — images are built on the server.

## Deploy

Automatic on push to `develop` or `main`, or via **Actions → Deploy → Run workflow**
(`workflow_dispatch`). Image tag: `<branch>-<shortsha>` (plus a `current` tag).

### First-deploy database bootstrap

On a fresh Postgres data dir, `db/init.sql` creates the **full** schema (it already
includes everything migrations `001`/`002` add). `deploy.sh` therefore runs
`alembic stamp head` on first deploy and `alembic upgrade head` thereafter — never a
blind `upgrade` on a fresh DB (which would fail on duplicate enum values).

## Manual run (from any machine with SSH access to the server)

```bash
# sync the repo + your .env to the server, then run the script there
rsync -az --exclude .git --exclude logs ./ forbes@217.217.253.76:/home/forbes/serve/scrapling-web/repo/
scp your.env forbes@217.217.253.76:/home/forbes/artifact/scrapling-web/.env
ssh forbes@217.217.253.76 'bash /home/forbes/serve/scrapling-web/repo/deploy/deploy.sh manual-1'
```

## Operations

- **Status:** `docker stack services scrapling-web`, `docker service ps scrapling-web_worker`
- **Logs:** `docker service logs -f scrapling-web_worker` (or the Dozzle UI on the box)
- **Roll back:** `docker service update --rollback scrapling-web_api`, or re-run the
  script with a previously-built tag (`deploy.sh <old-tag>`)
- **DB shell:** `docker exec -it $(docker ps -q -f name=scrapling-web_postgres) psql -U scraper -d social_scraper`

## Server notes / recommendations

- **RAM:** 8 GB total, ~3 GB free, **no swap**. Headless Chromium is memory-hungry.
  Keep the worker at 1 replica and consider adding swap:
  `sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile` (persist in `/etc/fstab`).
- **TLS:** the nginx vhost `scrap.acnenosystem.com` currently serves HTTP only —
  add HTTPS with `sudo certbot --nginx -d scrap.acnenosystem.com`, then set
  `CORS_ORIGINS=https://scrap.acnenosystem.com`.
- The old `/home/forbes/serve/scrapling-web` plain-compose copy is **superseded** by
  this stack and can be archived once the stack is verified.
