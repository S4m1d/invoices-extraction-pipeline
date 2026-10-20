# Pipeline stack (infra/README.md, Usage).
.PHONY: up up-fake down reset ps logs smoke check-env

# Without default build attestations an unchanged service builds to the same image ID, so a
# repeated `up` does not recreate its container.
export BUILDX_NO_DEFAULT_ATTESTATIONS := 1
COMPOSE := docker compose --env-file .env
FAKE := -f docker-compose.yml -f docker-compose.fake-llm.yml

check-env:
	@test -f .env || { echo "infra/.env is missing: copy .env.example to .env and set the passwords"; exit 1; }

up: check-env
	@test -f ../fields-extractor/.env || { echo "fields-extractor/.env is missing (ANTHROPIC_API_KEY); or use make up-fake"; exit 1; }
	$(COMPOSE) up -d --build --wait --remove-orphans

up-fake: check-env
	$(COMPOSE) $(FAKE) up -d --build --wait --remove-orphans

down: check-env
	$(COMPOSE) $(FAKE) down --remove-orphans

reset: check-env
	$(COMPOSE) $(FAKE) down --remove-orphans --volumes

ps: check-env
	$(COMPOSE) $(FAKE) ps -a

logs: check-env
	$(COMPOSE) $(FAKE) logs --tail 100 $(s)

smoke: check-env
	python3 smoke.py
