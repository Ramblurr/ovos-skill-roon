ARG TAG=dev
FROM docker.io/smartgic/ovos-skill-base:alpha

ARG BUILD_DATE=unknown
ARG VERSION=unknown

LABEL org.opencontainers.image.title="Open Voice OS OCI Roon skill image"
LABEL org.opencontainers.image.description="Control Roon"
LABEL org.opencontainers.image.version=${VERSION}
LABEL org.opencontainers.image.created=${BUILD_DATE}
LABEL org.opencontainers.image.documentation="https://github.com/ramblurr/ovos-roon-skill"
LABEL org.opencontainers.image.source="https://github.com/ramblurr/ovos-roon-skill"
LABEL org.opencontainers.image.vendor="Ramblurr"

USER root
RUN apk add --no-cache supervisor
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
USER ovos


RUN set -ex; \
    pip3 install git+https://github.com/ramblurr/ovos-skill-roon.git@${VERSION}; \
    rm -rf ${HOME}/.cache

COPY entrypoint.sh /entrypoint.sh

ENV ROON_PROXY_SOCK ipc:////skill_roon/roon_proxy.sock

ENTRYPOINT ["/entrypoint.sh"]
