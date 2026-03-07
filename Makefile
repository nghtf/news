.PHONY: install run run-test check reset-state telegram-ids docker-build docker-up docker-up-test docker-down docker-logs docker-reset-state

install:
	python3 -m pip install -r requirements.txt

run:
	NEWS_BOT_MODE=prod python3 -m src.bot

run-test:
	NEWS_BOT_MODE=test python3 -m src.bot

check:
	python3 -m py_compile src/*.py

reset-state:
	python3 -m src.reset_state

telegram-ids:
	python3 -m src.telegram_ids

docker-build:
	docker compose build

docker-up:
	NEWS_BOT_MODE=prod docker compose up -d

docker-up-test:
	NEWS_BOT_MODE=test docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f news-bot

docker-reset-state:
	docker compose run --rm news-bot python -m src.reset_state
