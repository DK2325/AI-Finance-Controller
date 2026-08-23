# Single-service image for hosted deployment (Railway).
#
# WHY THIS EXISTS ALONGSIDE docker/api.Dockerfile
#
# Locally, `docker compose up` is the documented run path and brings up three containers:
# db, api, web. That stays exactly as it is, because it is what BUILD.md promises and what
# a reviewer will run.
#
# Hosted, one service is cheaper, has one fewer thing that can be down during a demo, and
# removes a cross-origin hop -- so this image serves the API *and* the static frontend from
# one process. The two paths are independent, so neither can break the other.
#
# WHAT IS BAKED IN, AND WHY
#
# The trained model artifact and one scored run are committed to the repository and copied
# here. A cold start therefore has a completed run to show with no training, no database
# write, and no network call. "Live public URL, seeded with a completed run" is an exit
# criterion, and a cold start that trains a model is precisely the thing that fails in
# front of a panel.
#
# data/test is excluded by .dockerignore. It is sealed, and a sealed set that ships inside
# a public image is a seal that means nothing.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Native runtimes the wheels link against but do not carry.
#
# python:3.12-slim ships no OpenMP runtime. `pip install lightgbm` succeeds, `import
# lightgbm` succeeds, and the failure arrives only when a model is loaded:
#
#     OSError: libgomp.so.1: cannot open shared object file
#
# Found on the deployed host, on the one screen that loads a model, an hour after this
# image first built green. Nothing local could catch it -- a developer machine has libgomp,
# and so does every base image that is not slim.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, so source edits do not invalidate the pip layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml alembic.ini ./
COPY ledgerloop/ ./ledgerloop/
COPY datagen/ ./datagen/
COPY core/ ./core/
COPY model/ ./model/
COPY llm/ ./llm/
COPY api/ ./api/
COPY evals/ ./evals/
COPY web/static/ ./web/static/

RUN pip install --no-cache-dir -e . --no-deps

# Prove every native dependency LOADS AND RUNS, not merely that its wheel installed.
# A missing shared library now fails the build rather than a screen, which is the point:
# the same principle as every other guard here -- structurally impossible, not promised.
RUN python -m api.selfcheck

# The seed: a trained model and one scored run, both versioned deliverables.
COPY runs/_models/ ./runs/_models/
COPY runs/v1-train/ ./runs/v1-train/

# Batches the screens read: the demo batch for the one-click run, and the training batch
# because the review queue shows narrations and amounts as evidence.
COPY data/demo/ ./data/demo/
COPY data/train/ ./data/train/

# docker compose overrides the entrypoint with this to run migrations first. The hosted
# deployment does not: Railway's Postgres is managed and the demo reads runs from the
# filesystem, so a migration failure there would take the service down for something no
# screen needs.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Railway assigns $PORT and it is not 8000. Defaulted so the image also runs locally with
# a bare `docker run`.
ENV PORT=8000
EXPOSE 8000

# Shell form on purpose: $PORT must be expanded at runtime, and exec form would pass the
# literal string to uvicorn.
CMD uvicorn api.main:app --host 0.0.0.0 --port ${PORT}
