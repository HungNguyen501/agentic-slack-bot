ProjectName := Agentic Slack Bot
IMAGE := agentic-slack-bot
TAG ?= v2026.05.24

install:
	@uv sync --all-groups --active

lint:
	@ruff check . && flake8 --show-source --statistics .

build-image:
	docker buildx build -t $(IMAGE):$(TAG) -t $(IMAGE):latest .

compose-up:
	@docker compose up -d

compose-down:
	@docker compose down --remove-orphans

compose-down-clean:
	@docker compose down --volumes --remove-orphans

help:
	@echo "$(ProjectName)"