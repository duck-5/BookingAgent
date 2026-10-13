#!/bin/bash
pushd /home/mantin-server/BookingAgent || exit 1

# Clean up accidental directories created by Docker when files were missing
for item in system.log credentials.json client_secret.json token.json; do
    if [ -d "$item" ]; then
        echo "[WARNING] '$item' is a directory (created accidentally by Docker). Removing it..."
        rm -rf "$item"
    fi
done

# Ensure logs directory exists
mkdir -p logs

docker compose up
popd