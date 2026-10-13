#!/bin/bash
pushd /home/mantin-server/BookingAgent
if [ -d "system.log" ]; then
    rm -rf system.log
fi
docker compose up
popd