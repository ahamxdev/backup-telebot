FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install --no-install-recommends -y openssh-server python3 rsync \
    && useradd --create-home --shell /bin/sh backupbot \
    && passwd -d backupbot \
    && install -d -m 0755 /run/sshd /srv/backup/source/nested \
    && printf 'remote-root\n' > /srv/backup/source/root.txt \
    && printf 'remote-nested\n' > /srv/backup/source/nested/data.txt \
    && python3 -c "import sqlite3; c=sqlite3.connect('/srv/backup/source/state.sqlite3'); c.execute('CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT NOT NULL)'); c.execute(\"INSERT INTO records(value) VALUES ('remote-snapshot')\"); c.commit(); c.close()" \
    && chown -R backupbot:backupbot /srv/backup \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/ssh-test-entrypoint
RUN chmod 0755 /usr/local/bin/ssh-test-entrypoint

EXPOSE 22
ENTRYPOINT ["/usr/local/bin/ssh-test-entrypoint"]
