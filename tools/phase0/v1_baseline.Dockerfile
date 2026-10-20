# Same base image and system packages as v1's own Dockerfile (sentinel_flow_mvp/Dockerfile at v1-final).
# No Python packages are installed here: the runner installs v1's requirements.txt unchanged.
FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/work
RUN apt-get update && apt-get install -y libpq-dev gcc git postgresql-client \
    && rm -rf /var/lib/apt/lists/*
