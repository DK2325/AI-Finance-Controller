FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

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

RUN pip install --no-cache-dir -e . --no-deps

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
