FROM debian:bookworm-slim

# Install Samba AD DC and dependencies
RUN apt-get update && apt-get install -y \
    samba \
    samba-dsdb-modules \
    samba-vfs-modules \
    winbind \
    libnss-winbind \
    libpam-winbind \
    krb5-user \
    procps \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Expose necessary ports
# LDAP: 389, 636
# Kerberos: 88, 464
# SMB/AD: 135, 137, 138, 139, 445
# DNS: 53
# GC: 3268, 3269
EXPOSE 389 636 445 135 137/udp 138/udp 139 53 53/udp 88 88/udp 464 464/udp 3268 3269

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
