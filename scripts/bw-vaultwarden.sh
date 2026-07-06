#!/usr/bin/env bash
# Use the Bitwarden CLI version known to work with HodgeSpot Vaultwarden.
exec npx -y @bitwarden/cli@2024.12.0 "$@"
