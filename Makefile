.PHONY: up dev prod build test migrate migrate-stamp logs down

up:
	docker compose up -d

dev:
	docker compose up -d

prod:
	docker compose up -d

build:
	docker compose build backend edge-agent dashboard

test:
	docker compose run --rm backend pytest -q

migrate:
	docker compose run --rm backend alembic upgrade head

migrate-stamp:
	docker compose run --rm backend alembic stamp head

logs:
	docker compose logs -f --tail=100

down:
	docker compose down
