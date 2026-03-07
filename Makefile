.PHONY: install ensure-state clean run run-test check reset-state telegram-ids docker-build docker-up docker-up-test docker-down docker-logs docker-reset-state

install:
	python3 -m pip install -r requirements.txt

ensure-state:
	@if [ -d state.json ]; then \
		echo "Error: state.json is a directory. Remove it and create a file."; \
		echo "Run: rm -rf state.json && printf '{\"seen_links\":[],\"pending\":{}}\n' > state.json"; \
		exit 1; \
	fi
	@if [ ! -f state.json ]; then \
		printf '{"seen_links":[],"pending":{}}\n' > state.json; \
	fi

clean:
	@find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	@find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
	@rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage .coverage.*
	@if [ -d state.json ]; then rm -rf state.json; fi
	@printf '{"seen_links":[],"pending":{}}\n' > state.json
	@echo "Clean complete. state.json reset."

run: ensure-state
	NEWS_BOT_MODE=prod python3 -m src.bot

run-test: ensure-state
	NEWS_BOT_MODE=test python3 -m src.bot

check:
	python3 -m py_compile src/*.py

reset-state:
	python3 -m src.reset_state

telegram-ids:
	python3 -m src.telegram_ids

docker-build:
	docker compose build

docker-up: ensure-state
	NEWS_BOT_MODE=prod docker compose up -d

docker-up-test: ensure-state
	NEWS_BOT_MODE=test docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f news-bot

docker-reset-state:
	docker compose run --rm news-bot python -m src.reset_state
