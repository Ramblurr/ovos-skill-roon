#!/usr/bin/env sh
set -ex

pip3 install -e /mycroft_roon_skill

COMMAND=${1:-"both"}

if [ "$COMMAND" = "server" ]; then
    /home/ovos/.venv/bin/roon-proxy-server
elif [ "$COMMAND" = "skill" ]; then
    /home/ovos/.venv/bin/ovos-skill-launcher mycroft-roon-skill.ramblurr /mycroft_roon_skill
else
    /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
fi
