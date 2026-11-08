#!/bin/sh
# Trust any CA certificate mounted under /usr/local/share/ca-certificates/ (local Keycloak with a
# locally issued certificate), then run the given command.
set -e
if ls /usr/local/share/ca-certificates/*.crt >/dev/null 2>&1; then
    update-ca-certificates >/dev/null 2>&1 || true
    export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
    export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
fi
exec "$@"
