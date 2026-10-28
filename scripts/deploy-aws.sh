#!/bin/sh
set -eu
cd /opt/fiap-switch
test -f .env.production
test -d /srv/fiap-switch/data
APP_ENV_FILE=.env.production docker compose -p fiap-switch --env-file .env.production -f compose.yml -f compose.aws.yml up -d --build
