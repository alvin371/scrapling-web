# Social Scraper Service

A microservice for scraping social media profiles and posts (Instagram, Threads) via an async job queue.

## Architecture

```
┌─────────────┐     ┌─────────┐     ┌──────────────┐
│  FastAPI API │────▶│  Redis  │────▶│  RQ Worker   │
│  (port 8000) │     │  Queue  │     │  (Scrapling) │
└──────┬──────┘     └─────────┘     └──────┬───────┘
       │                                    │
       └──────────────┬─────────────────────┘
                      ▼
              ┌───────────────┐
              │  PostgreSQL   │
              │  (port 5432)  │
              └───────────────┘
```

- **API** — FastAPI app exposing REST endpoints for managing accounts, jobs, and posts
- **Worker** — RQ worker that processes scrape jobs using [Scrapling](https://github.com/D4Vinci/Scrapling)
- **RQ Dashboard** — Job monitoring UI at `http://localhost:9181`

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose

## Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd social-scraper-service
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in the required values:

| Variable | Required | Description |
|----------|----------|-------------|
| `POSTGRES_USER` | Yes | PostgreSQL username |
| `POSTGRES_PASSWORD` | Yes | PostgreSQL password |
| `POSTGRES_DB` | Yes | PostgreSQL database name |
| `SECRET_KEY` | Yes | App secret key (change in production) |
| `API_KEYS` | Yes | Comma-separated API keys for authentication |
| `INSTAGRAM_SESSION_ID` | Yes | Instagram `sessionid` cookie for authenticated scraping |
| `THREADS_SESSION_ID` | No | Threads session cookie (required for view counts) |
| `SCRAPLING_PROXY` | No | Proxy URL for stealth fetcher (e.g. `http://user:pass@host:port`) |

**Generate a secure API key:**
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**Get your Instagram session cookie:**
1. Log in to Instagram in your browser
2. Open DevTools → Application → Cookies → `https://www.instagram.com`
3. Copy the value of the `sessionid` cookie

### 3. Start all services

```bash
make up
```

This starts: PostgreSQL, Redis, API, Worker, and RQ Dashboard.

### 4. Run database migrations

```bash
make migrate
```

### 5. Verify the service is running

```bash
# Check service health
curl http://localhost:8000/api/v1/health

# Check running containers
make ps
```

## Usage

All protected endpoints require the `X-API-Key` header.

### Create a scrape job

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "instagram",
    "job_type": "profile",
    "target": "username",
    "queue": "default"
  }'
```

**Platforms:** `instagram`, `threads`

**Job types:**
| Type | Description |
|------|-------------|
| `profile` | Scrape account profile info |
| `posts` | Scrape list of posts |
| `posts_detail` | Scrape full post details |
| `post` | Scrape a single post by URL |
| `followers` | Scrape followers list |
| `following` | Scrape following list |

**Queues:** `high`, `default`, `low`

### Check job status

```bash
curl http://localhost:8000/api/v1/jobs/<job-id> \
  -H "X-API-Key: your-api-key"
```

### List jobs

```bash
curl "http://localhost:8000/api/v1/jobs?status=completed&platform=instagram&limit=20" \
  -H "X-API-Key: your-api-key"
```

### API docs

Interactive Swagger UI: `http://localhost:8000/docs`

## Make Commands

| Command | Description |
|---------|-------------|
| `make up` | Start all services in background |
| `make down` | Stop all services |
| `make build` | Rebuild Docker images (no cache) |
| `make migrate` | Run Alembic database migrations |
| `make logs` | Tail logs for all services |
| `make logs-worker` | Tail worker logs only |
| `make ps` | Show running containers |
| `make shell-api` | Open shell in API container |
| `make shell-db` | Open psql in database container |

## Development

### View logs

```bash
# All services
make logs

# Worker only
make logs-worker
```

### Monitor jobs

Open `http://localhost:9181` for the RQ Dashboard.

### Database shell

```bash
make shell-db
```

### Add a migration

```bash
docker compose exec -w /project api alembic revision --autogenerate -m "description"
make migrate
```
