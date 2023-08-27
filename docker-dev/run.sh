#!/usr/bin/env sh

VERSION=dev
OVOS_USER=ovos
TMP_FOLDER=~/src/ovos/data/tmp
CONFIG_FOLDER=~/src/ovos/data/config
SKILL_FOLDER=~/src/mycroft/roon-skill


podman run --userns=keep-id \
    -v ${SKILL_FOLDER}:/skill_roon:Z \
    -v ${CONFIG_FOLDER}:/home/${OVOS_USER}/.config/mycroft:Z \
    -v ${TMP_FOLDER}:/tmp/mycroft:Z \
    -it \
    --name ramblurr_skill_roon \
    --rm \
    --network=host \
    --entrypoint /bin/sh \
    docker.io/library/docker-ramblurr_skill_roon
    #--ipc=host \
    #--privileged \
