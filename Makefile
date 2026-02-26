up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build --no-cache

logs:
	docker compose logs -f

logs-worker:
	docker compose logs -f worker

migrate:
	docker compose exec -w /project api alembic upgrade head

shell-api:
	docker compose exec api bash

shell-db:
	docker compose exec postgres psql -U scraper -d social_scraper

ps:
	docker compose ps
