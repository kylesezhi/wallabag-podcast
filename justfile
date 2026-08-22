# wallabag-podcast task runner

# List available recipes
default:
    @just --list

# Run the dev server (http://127.0.0.1:8000)
run:
    #!/usr/bin/env bash
    if [ ! -f .env ]; then
        echo "No .env found — creating one from .env.example."
        echo "Fill in your Wallabag credentials, then re-run 'just run'."
        cp .env.example .env
        exit 1
    fi
    exec uv run uvicorn app.main:app --reload

# Run the test suite
test:
    uv run pytest -q
