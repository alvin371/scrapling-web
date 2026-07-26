# Deployment (Docker Swarm + GitHub Actions + Ansible)

This service deploys to the server as a **Docker Swarm stack** named `scrapling-web`,
matching the convention used by the other apps on the box (`forbes`, `sec-forbes`,
`mysql-8`, `dozzle`). CI builds images, pushes them to Docker Hub, and an Ansible
playbook deploys the stack over SSH.

```
push develop/main ─▶ GitHub Actions ─▶ build & push images (Docker Hub)
                                    └─▶ Ansible ─ssh▶ docker stack deploy scrapling-web
```

## Topology on the server

| Service      | Image                              | Port (host)         | Notes                                   |
|--------------|------------------------------------|---------------------|-----------------------------------------|
| postgres     | `postgres:15-alpine`               | internal            | **dedicated** DB for this app           |
| redis        | `redis:7-alpine`                   | internal            | append-only persistence                 |
| api          | `<DOCKER_USERNAME>/scrapling-web-api`   | `18000` → nginx `scrap.acnenosystem.com` |
| worker       | `<DOCKER_USERNAME>/scrapling-web-worker`| internal            | Scrapling + headless Chromium           |
| rq-dashboard | `eoranged/rq-dashboard`            | `19181`             | **unauthenticated** — front with nginx auth before exposing |

- The server has **only MySQL** (used by the PHP apps); this app is PostgreSQL-only,
  so it ships its **own** Postgres. They coexist.
- Persistence uses host bind-mounts under `/home/forbes/serve/scrapling-web/`
  (`postgres-data/`, `redis-data/`, `logs/`).
- App secrets live in `/home/forbes/artifact/scrapling-web/.env`, rendered by Ansible
  from CI secrets and bind-mounted into the api/worker containers (Swarm ignores
  `env_file:`, so the file is mounted, not injected).

## Required GitHub Actions secrets

Add these under **Settings → Secrets and variables → Actions** (the repo currently has
none). Deploy secrets:

| Secret               | Example / source                                            |
|----------------------|------------------------------------------------------------|
| `DOCKER_USERNAME`    | Docker Hub user — images push to `<user>/scrapling-web-*`   |
| `DOCKER_PASSWORD`    | Docker Hub access token                                     |
| `ANSIBLE_PRIVATE_KEY`| SSH **private** key whose public half is in `forbes`' `~/.ssh/authorized_keys` |
| `SERVER_HOST`        | `217.217.253.76`                                            |

App / DB secrets (rendered into the server `.env`):

| Secret                 | Notes                                                       |
|------------------------|------------------------------------------------------------|
| `POSTGRES_USER`        | e.g. `scraper`                                              |
| `POSTGRES_PASSWORD`    | strong password (dedicated Postgres)                        |
| `POSTGRES_DB`          | e.g. `social_scraper`                                       |
| `SECRET_KEY`           | `python -c "import secrets; print(secrets.token_hex(32))"`  |
| `API_KEYS`             | comma-separated client keys                                 |
| `INSTAGRAM_SESSION_ID` | Instagram `sessionid` cookie                                |
| `CORS_ORIGINS`         | optional — e.g. `https://scrap.acnenosystem.com` (empty ⇒ `*`) |
| `THREADS_SESSION_ID`   | optional                                                    |
| `SCRAPLING_PROXY`      | optional                                                    |

> Note: the older `ANSIBLE_HOSTS` secret from your other projects is **not** used —
> this workflow builds the inventory from `SERVER_HOST` + user `forbes`.

## Deploy

Automatic on push to `develop` or `main`, or via **Actions → Build and Deploy → Run
workflow** (`workflow_dispatch`).

Image tags: `<branch>-<shortsha>` (immutable, used for the deploy) plus a moving
`<branch>` tag.

### First-deploy database bootstrap

On a fresh Postgres data dir, `db/init.sql` creates the **full** schema (it already
includes everything migrations `001`/`002` add). The playbook therefore runs
`alembic stamp head` on first deploy and `alembic upgrade head` thereafter — never a
blind `upgrade` on a fresh DB (which would fail on duplicate enum values).

## Manual run (from a machine with SSH access to the server)

```bash
cd deploy/ansible
cp inventory.example.ini inventory.ini   # edit host/key
ansible-playbook deploy.yml \
  -e registry_user=<dockerhub-user> -e docker_password=<token> \
  -e image_tag=<branch>-<sha> \
  -e postgres_user=scraper -e postgres_password=<pw> -e postgres_db=social_scraper \
  -e secret_key=<key> -e api_keys=<keys> -e instagram_session_id=<cookie>
```

## Operations

- **Status:** `docker stack services scrapling-web`, `docker service ps scrapling-web_worker`
- **Logs:** `docker service logs -f scrapling-web_worker` (or the Dozzle UI already on the box)
- **Roll back:** re-run the workflow pinned to a previous image tag, or
  `docker service update --rollback scrapling-web_api`
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
