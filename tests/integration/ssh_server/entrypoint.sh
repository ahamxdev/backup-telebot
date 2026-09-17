#!/bin/sh
set -eu

install -d -m 0700 -o backupbot -g backupbot /home/backupbot/.ssh
install -m 0600 -o backupbot -g backupbot /run/test-keys/client.pub /home/backupbot/.ssh/authorized_keys
install -m 0600 /run/test-keys/host_key /etc/ssh/ssh_host_ed25519_key
install -m 0644 /run/test-keys/host_key.pub /etc/ssh/ssh_host_ed25519_key.pub

cat > /etc/ssh/sshd_config <<'EOF'
Port 22
ListenAddress 0.0.0.0
HostKey /etc/ssh/ssh_host_ed25519_key
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin no
UsePAM no
AllowUsers backupbot
AuthorizedKeysFile .ssh/authorized_keys
EOF

exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config
