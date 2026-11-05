.PHONY: up down kind-up kind-down helm-lint helm-template compose-validate

COMPOSE := docker compose -f deploy/compose/compose.yaml
COMPOSE_PROFILES_ALL := --profile core --profile app --profile observability
CHART := deploy/helm/xstore

up:
	$(COMPOSE) $(COMPOSE_PROFILES_ALL) up -d

down:
	$(COMPOSE) $(COMPOSE_PROFILES_ALL) --profile otel down

kind-up:
	kind create cluster --config deploy/kind/kind-config.yaml
	-docker network connect kind kind-registry

kind-down:
	kind delete cluster --name xstore

helm-lint:
	helm lint --strict $(CHART)
	helm lint --strict $(CHART) -f $(CHART)/values-eks.yaml
	helm lint --strict $(CHART) -f $(CHART)/values-aks.yaml

helm-template:
	helm template xstore $(CHART) > /dev/null
	helm template xstore $(CHART) -f $(CHART)/values-eks.yaml > /dev/null
	helm template xstore $(CHART) -f $(CHART)/values-aks.yaml > /dev/null

compose-validate:
	$(COMPOSE) $(COMPOSE_PROFILES_ALL) --profile otel config --quiet
