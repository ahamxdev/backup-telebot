FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_NO_CACHE=1

ARG DEBIAN_SNAPSHOT=20260831T000000Z
RUN sed -i \
      -e "s|http://deb.debian.org/debian-security|https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}|g" \
      -e "s|http://deb.debian.org/debian|https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}|g" \
      /etc/apt/sources.list.d/debian.sources \
    && printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99snapshot \
    && apt-get update \
    && apt-get install --no-install-recommends -y \
       age bzip2 gzip liblz4-tool mariadb-client openssh-client p7zip-full postgresql-client-15 \
       rsync unzip xz-utils zip zstd \
    && dpkg-query -W -f='${Package}=${Version}\n' \
       age bzip2 gzip liblz4-tool mariadb-client openssh-client p7zip-full postgresql-client-15 \
       rsync unzip xz-utils zip zstd > /usr/local/share/backup-toolchain.lock \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.10@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev \
    && groupadd --gid 10001 backupbot \
    && useradd --uid 10001 --gid backupbot --no-create-home --home-dir /app backupbot \
    && mkdir -p /data/runtime/data /data/runtime/telegram \
       /data/runtime/work /data/runtime/pending-delivery \
    && chown -R backupbot:backupbot /app /data

ENV PATH="/app/.venv/bin:$PATH"
USER 10001:10001
CMD ["backup-bot"]
