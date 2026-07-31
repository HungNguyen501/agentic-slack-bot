ProjectName := Agentic Slack Bot
DOCKER_REPO := hungwnguyen
IMAGE := agentic-slack-bot
TAG ?= v2026.06.28

install:
	@uv sync --all-groups --active

lint:
	@ruff check . && flake8 --show-source --statistics .

build-image:
	docker buildx build -t $(IMAGE):$(TAG) -t $(IMAGE):latest .

compose-up:
	@docker compose up -d --build

compose-down:
	@docker compose down --remove-orphans

compose-down-clean:
	@docker compose down --volumes --remove-orphans

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
	@echo "  make install              Install dependencies (uv sync --all-groups --active)"
	@echo "  make lint                 Run ruff + flake8"
	@echo "  make build-image          Build local image, tagged \$$(TAG) and latest"
	@echo "                              e.g. make build-image TAG=v2026.07.31"
	@echo "  make compose-up           docker compose up -d --build"
	@echo "  make compose-down         docker compose down --remove-orphans"
	@echo "  make compose-down-clean   docker compose down --volumes --remove-orphans"
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