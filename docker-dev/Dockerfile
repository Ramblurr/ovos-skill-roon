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

ARG DEV=false

#RUN if [ "${DEV}" == "true" ]; then \
#    pip3 install git+https://github.com/ramblurr/ovos-skill-roon.git; \
#    else \
#    pip3 install git+https://github.com/ramblurr/ovos-skill-roon.git; \
#    fi \
#    && rm -rf ${HOME}/.cache
#
#RUN pip3 install -e /roon-skill && rm -rf ${HOME}/.cache
RUN rm -rf ${HOME}/.cache
VOLUME /roon-skill

COPY entrypoint.sh /entrypoint.sh

ENV ROON_PROXY_SOCK ipc:////mycroft_roon_skill/roon_proxy.sock

#ENTRYPOINT ["ovos-skill-launcher", "mycroft-roon-skill.ramblurr"]
ENTRYPOINT ["/entrypoint.sh"]
