FROM python:3.12.4-slim-bookworm as python-base

# Python ENV vars, valid in all images that use 'FROM python-base'
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=on \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    POETRY_NO_INTERACTION=1 \
    DEBIAN_FRONTEND=noninteractive \
    SETUP_PATH=/opt \
    CRAWLER_PATH=/crawler/ \
    POETRY_VIRTUALENVS_CREATE=false \
    LANG=C.UTF-8

# Based on the ones above
ENV VIRTUAL_ENV=$SETUP_PATH/venv \
    PATH=$SETUP_PATH/venv/bin:$PATH

# Merge build env and prod for now
RUN apt-get update && \
    apt-get install -y wget unzip python3-pip vim && \
    apt-get install -y build-essential git pkg-config libpq-dev

# Copy needed files
COPY install_uc.sh poetry.lock pyproject.toml run_consent_crawl_uc.py *.tar.gz README.md $CRAWLER_PATH
COPY crawler $CRAWLER_PATH/crawler/

WORKDIR $CRAWLER_PATH

RUN pip install poetry && poetry install && \
    poetry cache clear --all -n . && \
    rm -rf /root/.cache
