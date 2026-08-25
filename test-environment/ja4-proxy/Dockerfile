# JA4/TLS fingerprinting proxy -- docs/REQUIREMENTS.md section 9 entry
# 17 (F5-class multi-signal layer, claude/ja4-experiment branch). See
# haproxy.cfg's own header comment for the full architecture.
#
# HAProxy 3.2+ required (ja4.lua's own documented minimum) -- the
# official image is built with USE_LUA=1 (confirmed by reading
# docker-library/haproxy's own generated Dockerfile), so no extra Lua
# install is needed.
#
# Multi-stage build: the official haproxy image strips its own
# build-only dependencies (including the standalone `openssl` CLI --
# only the linked library haproxy itself uses survives) from the final
# runtime image, confirmed by reading docker-library/haproxy's own
# Dockerfile (`apk del --no-network .build-deps` removes it after
# haproxy is compiled). A separate, throwaway stage that definitely has
# `openssl` generates the self-signed cert; only the resulting PEM is
# copied into the real haproxy stage.
FROM alpine:3.22 AS certgen
RUN apk add --no-cache openssl && \
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout /key.pem -out /cert.pem -days 3650 -subj "/CN=localhost" && \
    cat /cert.pem /key.pem > /combined.pem

FROM haproxy:3.2

USER root
COPY --from=certgen --chown=haproxy:haproxy /combined.pem /usr/local/etc/haproxy/certs/combined.pem
RUN chmod 400 /usr/local/etc/haproxy/certs/combined.pem
COPY haproxy.cfg /usr/local/etc/haproxy/haproxy.cfg
COPY ja4.lua /usr/local/etc/haproxy/ja4.lua

USER haproxy
