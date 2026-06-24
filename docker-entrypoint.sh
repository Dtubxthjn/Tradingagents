#!/bin/sh
set -e

if [ "${WEB_MODE}" = "true" ]; then
    exec python web/app.py
else
    exec tradingagents "$@"
fi
