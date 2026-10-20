# Flyby landing page
#
#   make          serve the site at http://localhost:3000 and open it
#   make test     static checks + headless-Chrome smoke tests (desktop & mobile)
#   make help     list targets
#
# Override defaults inline, e.g. `make PORT=8080` or `make test CHROME=/path/to/chrome`.

PORT   ?= 3000
PYTHON ?= python3
OPEN   ?= $(shell command -v open || command -v xdg-open)

.DEFAULT_GOAL := serve
.PHONY: serve test help

serve: ## Serve the site locally and open it in your browser
	@echo "Serving http://localhost:$(PORT)  (Ctrl+C to stop)"
	@if [ -n "$(OPEN)" ]; then (sleep 1 && "$(OPEN)" "http://localhost:$(PORT)" >/dev/null 2>&1) & fi
	@$(PYTHON) -m http.server $(PORT) --bind 127.0.0.1

test: ## Run static checks and headless-browser smoke tests
	@$(PYTHON) tests/smoke.py

help: ## List available targets
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-6s %s\n", $$1, $$2}'
