ProjectName := Agentic Slack Bot
DOCKER_REPO := hungwnguyen
IMAGE := agentic-slack-bot
TAG ?= v2026.09.11
FLYWAY_IMAGE := flyway/flyway:11-alpine
FLYWAY_URL = $(shell . ./.env && python3 -c "from urllib.parse import urlparse, unquote; u = urlparse('$${SUPABASE_DB_URL}'); print(f'jdbc:postgresql://{u.hostname}:{u.port or 5432}{u.path}?user={unquote(u.username)}&password={unquote(u.password)}')")

install:
	@uv sync --all-groups --active \
		&& pre-commit install

lint:
	@ruff check . && flake8 --show-source --statistics .

lint-sql:
	@sqlfluff lint src/migrations/ src/databricks/metric_views/

build-image:
	docker buildx build -t $(IMAGE):$(TAG) -t $(IMAGE):latest .

compose-up:
	@docker compose up -d --build

compose-down:
	@docker compose down --remove-orphans

compose-down-clean:
	@docker compose down --volumes --remove-orphans

db-migrate:
	@docker run --rm -v $(PWD)/src/migrations:/flyway/sql $(FLYWAY_IMAGE) -url="$(FLYWAY_URL)" migrate

db-migrate-info:
	@docker run --rm -v $(PWD)/src/migrations:/flyway/sql $(FLYWAY_IMAGE) -url="$(FLYWAY_URL)" info

db-migrate-validate:
	@docker run --rm -v $(PWD)/src/migrations:/flyway/sql $(FLYWAY_IMAGE) -url="$(FLYWAY_URL)" validate

db-migrate-baseline:
	@docker run --rm -v $(PWD)/src/migrations:/flyway/sql $(FLYWAY_IMAGE) -url="$(FLYWAY_URL)" -baselineVersion=$(or $(VERSION),4) baseline

ecr-login:
	@docker login -u hungwnguyen

docker-build-push: ecr-login
	@docker buildx build --platform linux/amd64 -t $(DOCKER_REPO)/$(IMAGE):$(TAG) --push .

ansible-deploy:
	@cd ansible && ansible-playbook playbooks/deploy.yml -i inventory/hosts.ini -vv

ansible-deploy-check:
	@cd ansible && ansible-playbook playbooks/deploy.yml -i inventory/hosts.ini --check -vv

ansible-restart:
	@cd ansible && ansible-playbook playbooks/deploy.yml -i inventory/hosts.ini --tags deploy_restart -vv

ansible-stop:
	@cd ansible && ansible-playbook playbooks/stop.yml -i inventory/hosts.ini -vv

ansible-sync-skills:
	@cd ansible && ansible-playbook playbooks/sync-skills.yml -i inventory/hosts.ini -vv

ansible-logs:
	@cd ansible && ansible-playbook playbooks/logs.yml -i inventory/hosts.ini -e log_service=$(SVC) -e log_tail=$(or $(TAIL),50)

help:
	@echo "$(ProjectName)"
	@echo ""
	@echo "Local development:"
	@echo "  make install              Install dependencies + pre-commit hooks (make lint, make lint-sql)"
	@echo "  make lint                 Run ruff + flake8"
	@echo "  make lint-sql             Run sqlfluff against migrations + metric views"
	@echo "  make build-image          Build local image, tagged \$$(TAG) and latest"
	@echo "                              e.g. make build-image TAG=v2026.07.31"
	@echo "  make compose-up           docker compose up -d --build"
	@echo "  make compose-down         docker compose down --remove-orphans"
	@echo "  make compose-down-clean   docker compose down --volumes --remove-orphans"
	@echo ""
	@echo "Database migrations (Flyway, reads SUPABASE_DB_URL from .env):"
	@echo "  make db-migrate              Apply pending migrations in src/migrations/"
	@echo "  make db-migrate-info         Show applied/pending migration status"
	@echo "  make db-migrate-validate     Validate applied migrations against local files"
	@echo "  make db-migrate-baseline     Mark schema as already at VERSION (default 4)"
	@echo "                                 without running its migration, for a DB whose"
	@echo "                                 tables predate Flyway adoption"
	@echo "                                 e.g. make db-migrate-baseline VERSION=4"
	@echo ""
	@echo "Image publishing:"
	@echo "  make ecr-login            docker login -u hungwnguyen"
	@echo "  make docker-build-push    Build (linux/amd64) and push \$$(TAG) to Docker Hub"
	@echo "                              e.g. make docker-build-push TAG=v2026.07.31"
	@echo ""
	@echo "Remote deployment (ansible, targets vm-ai-job-2):"
	@echo "  make ansible-deploy         Full deploy: copy files, render compose, pull image,"
	@echo "                              start services, remove remote .env when done"
	@echo "  make ansible-deploy-check   Dry-run of ansible-deploy (--check, no changes made)"
	@echo "  make ansible-restart        Restart services on the remote host without re-pulling"
	@echo "                              (--tags deploy_restart, does not touch .env)"
	@echo "  make ansible-stop           Stop all remote services (docker compose down)"
	@echo "  make ansible-sync-skills    Copy src/worker/skills/ to the remote host only"
	@echo ""
	@echo "  make ansible-logs           Fetch remote logs (last 50 lines, all services)"
	@echo "  make ansible-logs SVC=worker              Fetch logs for one service"
	@echo "  make ansible-logs SVC=worker TAIL=200      Fetch last 200 lines for one service"