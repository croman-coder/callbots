.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE     := docker compose
COMPOSE_GPU := docker compose -f docker-compose.yml -f docker-compose.gpu.yml

.PHONY: help setup models up up-gpu down restart logs logs-api logs-agent logs-asterisk \
        ps build discover seed shell-api shell-db migrate migration sync test-call \
        asterisk-cli sip-status clean reset

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- preparación
setup: ## Crea el .env y descarga la voz de Piper
	@test -f .env || (cp .env.example .env && echo "Creado .env — completá BITRIX_WEBHOOK_URL y las passwords")
	@$(MAKE) models

models: ## Descarga la voz de Piper
	@chmod +x scripts/download_models.sh
	@./scripts/download_models.sh $${PIPER_VOICE:-es_AR-daniela-high}

build: ## Reconstruye las imágenes
	$(COMPOSE) build

# ------------------------------------------------------------------ ejecución
up: ## Levanta todo (CPU)
	$(COMPOSE) up -d
	@echo ""
	@echo "Panel:  http://localhost:$${API_PORT:-8000}"
	@echo "Docs:   http://localhost:$${API_PORT:-8000}/docs"

up-gpu: ## Levanta todo usando la GPU NVIDIA
	$(COMPOSE_GPU) up -d
	@echo ""
	@echo "Panel:  http://localhost:$${API_PORT:-8000}"

down: ## Detiene todo
	$(COMPOSE) down

restart: ## Reinicia los servicios de aplicación
	$(COMPOSE) restart api worker beat voice-agent

ps: ## Estado de los contenedores
	$(COMPOSE) ps

# --------------------------------------------------------------------- logs
logs: ## Logs de todo
	$(COMPOSE) logs -f --tail=100

logs-api: ## Logs de la API y el worker
	$(COMPOSE) logs -f --tail=100 api worker beat

logs-agent: ## Logs del voice-agent
	$(COMPOSE) logs -f --tail=100 voice-agent

logs-asterisk: ## Logs de Asterisk
	$(COMPOSE) logs -f --tail=100 asterisk

# ------------------------------------------------------------------- Bitrix
discover: ## Lista las Smart Processes de tu portal
	$(COMPOSE) exec api python scripts/bitrix_discover.py

fields: ## Campos de una entidad: make fields ID=1036
	@test -n "$(ID)" || (echo "Falta ID. Uso: make fields ID=1036"; exit 1)
	$(COMPOSE) exec api python scripts/bitrix_discover.py $(ID) --sample

seed: ## Crea la campaña de ejemplo (pausada)
	$(COMPOSE) exec api python scripts/seed_campaign.py

sync: ## Fuerza la sincronización con Bitrix ahora
	$(COMPOSE) exec worker celery -A app.scheduler.worker.celery_app call callbot.sync_bitrix

# --------------------------------------------------------------- base de datos
migrate: ## Aplica las migraciones pendientes
	$(COMPOSE) exec api alembic upgrade head

migration: ## Genera una migración: make migration M="agrega campo x"
	@test -n "$(M)" || (echo 'Falta M. Uso: make migration M="descripción"'; exit 1)
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(M)"

shell-db: ## Consola de Postgres
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-callbot} -d $${POSTGRES_DB:-callbot}

shell-api: ## Shell dentro del contenedor de la API
	$(COMPOSE) exec api bash

# ----------------------------------------------------------------- telefonía
asterisk-cli: ## Consola de Asterisk
	$(COMPOSE) exec asterisk asterisk -rvvv

sip-status: ## Estado de los softphones registrados
	$(COMPOSE) exec asterisk asterisk -rx "pjsip show endpoints"

test-call: ## Recordatorio de cómo probar la encuesta
	@echo "Para probar sin llamar a nadie:"
	@echo "  1. Registrá Linphone/Zoiper:"
	@echo "       usuario:  softphone-1"
	@echo "       password: \$$SOFTPHONE_PASSWORD (del .env)"
	@echo "       servidor: la IP de este host, puerto $${SIP_PORT:-5060}"
	@echo "  2. Marcá 9001 -> eco, verifica que el audio va y viene"
	@echo "  3. Marcá 9000 -> corre la encuesta de la campaña activa (modo demo)"
	@echo ""
	@$(MAKE) sip-status

# -------------------------------------------------------------------- limpieza
clean: ## Baja todo y borra los volúmenes de datos (NO los modelos)
	$(COMPOSE) down -v

reset: clean up migrate ## Reinicio total con base limpia
	@echo "Base recreada."
