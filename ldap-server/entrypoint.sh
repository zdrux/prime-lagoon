#!/bin/bash
set -e

# Configuration variables
DOMAIN=${DOMAIN:-LAB.LOCAL}
REALM=${REALM:-LAB.LOCAL}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-Admin123!}
LDAP_USER=${LDAP_USER:-testuser}
LDAP_PASSWORD=${LDAP_PASSWORD:-Password123!}

SAMBA_DIR="/var/lib/samba"
CONFIG_FILE="/etc/samba/smb.conf"

# Provision the domain if it doesn't exist on the persistent volume
if [ ! -f "$SAMBA_DIR/private/sam.ldb" ]; then
    echo "--- Provisioning Samba AD DC for domain $DOMAIN ---"
    
    # Remove default config if it exists
    rm -f $CONFIG_FILE
    
    # Provision domain
    samba-tool domain provision \
        --use-rfc2307 \
        --domain="$DOMAIN" \
        --realm="$REALM" \
        --server-role=dc \
        --dns-backend=SAMBA_INTERNAL \
        --adminpass="$ADMIN_PASSWORD" \
        --option="workgroup=$DOMAIN" \
        --option="realm=$REALM"
    
    echo "--- Creating test user $LDAP_USER ---"
    samba-tool user create "$LDAP_USER" "$LDAP_PASSWORD"
    
    # Add domain prefix to help with NTLM testing
    echo "--- Domain $DOMAIN provisioned successfully ---"
fi

# Start Samba in foreground
echo "--- Starting Samba AD DC ---"
exec samba -i
