# Local MongoDB for development — wraps docker-compose.dev.yml.
# See docs/development.md. The default host port (27018) never collides with a
# MongoDB already running on 27017; override with MONGO_PORT.

COMPOSE := docker compose -f docker-compose.dev.yml

.PHONY: mongo-up mongo-down mongo-reset mongo-logs mongo-load mongo-load-raw mongo-snapshot

## start MongoDB (data survives `make mongo-down`)
mongo-up:
	$(COMPOSE) up -d

## stop MongoDB, keep the data volume
mongo-down:
	$(COMPOSE) down

## stop MongoDB and DELETE the data volume (start fresh)
mongo-reset:
	$(COMPOSE) down -v

## follow MongoDB logs
mongo-logs:
	$(COMPOSE) logs -f mongo

## import the SANITIZED data/ tree into MongoDB (no GitHub token needed)
mongo-load:
	@if [ ! -d data/bronze ]; then ./scripts/data-snapshot.sh unpack --force; fi
	./scripts/load_mongo_snapshot.sh

## import the PRIVATE raw corpus (cache/) into MongoDB — offline dev only
mongo-load-raw:
	@if [ ! -d cache ]; then ./scripts/data-snapshot.sh unpack --force; fi
	./scripts/load_mongo_snapshot.sh --raw

## archive the local corpus (cache/ + data/) for reuse across worktrees
mongo-snapshot:
	./scripts/data-snapshot.sh pack
