#!/usr/bin/env sh
set -ex

pip3 install -e /skill_roon

COMMAND=${1:-"both"}

if [ "$COMMAND" = "server" ]; then
    /home/ovos/.venv/bin/roon-proxy-server
elif [ "$COMMAND" = "skill" ]; then
    /home/ovos/.venv/bin/ovos-skill-launcher skill-roon.ramblurr /skill_roon
else
    /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
fi
