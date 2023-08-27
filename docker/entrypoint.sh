#!/usr/bin/env sh
set -ex

COMMAND=${1:-"both"}

if [ "$COMMAND" = "server" ]; then
    /home/ovos/.venv/bin/roon-proxy-server
elif [ "$COMMAND" = "skill" ]; then
    /home/ovos/.venv/bin/ovos-skill-launcher skill-roon.ramblurr
else
    /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
fi
