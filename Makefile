REGION ?= mallorca_pilot

install:
	uv sync
lint:
	uv run ruff check .
	uv run mypy src
test:
	uv run pytest
inspect:
	uv run coastscan inspect-inputs --region $(REGION)
build:
	uv run coastscan build-region --region $(REGION)
qa:
	uv run coastscan build-region --region $(REGION) --force
