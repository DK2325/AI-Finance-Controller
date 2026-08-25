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

# The seed: a trained model and two scored runs, all versioned deliverables.
#
# v1-test is the one the screens open on. It is the sealed held-out set scored at the
# pre-committed threshold, which makes the live demo the same evidence the README reports
# rather than a friendlier run alongside it. v1-train is kept as a fallback so a cold boot
# still has something to show if the test run is ever absent.
COPY runs/_models/ ./runs/_models/
COPY runs/v1-train/ ./runs/v1-train/
COPY runs/v1-test/ ./runs/v1-test/

# Batches the screens read: the demo batch for the one-click run, the training batch
# because the review queue shows narrations and amounts as evidence, and the held-out set
# because it is what the seeded run is over.
#
# data/test/.unsealed ships with it deliberately: it carries the sha256 map forward from
# the deleted .sealed marker, so the integrity chain holds inside the image and not only
# in the repository. A reviewer who pulls this image can check that the numbers on the
# screen were computed from the bytes that were sealed.
COPY data/demo/ ./data/demo/
COPY data/train/ ./data/train/
COPY data/test/ ./data/test/

# Fail the build if the integrity record did not travel. Without the marker the served
# numbers are still correct and no longer *checkable*, and an unverifiable claim inside a
# shipped artifact is exactly the thing this project keeps refusing to make.
RUN test -f ./data/test/.unsealed || (echo "data/test/.unsealed missing from image" && exit 1)

# There is no entrypoint script. There was one, invoked only by `docker compose`, which
# ran migrations before serving; the hosted deployment ran the CMD below and never applied
# them, so Postgres was reachable and empty and every approval on the live site fell
# through to the file store. `api.main` applies migrations on start instead -- non-fatally,
# which preserves the reason the hosted path skipped them in the first place -- so both run
# paths get the schema from the same image rather than from two files kept in step.

# Railway assigns $PORT and it is not 8000. Defaulted so the image also runs locally with
# a bare `docker run`.
ENV PORT=8000
EXPOSE 8000

# Shell form on purpose: $PORT must be expanded at runtime, and exec form would pass the
# literal string to uvicorn.
CMD uvicorn api.main:app --host 0.0.0.0 --port ${PORT}
