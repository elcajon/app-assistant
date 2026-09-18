ARG BUILD_FROM=ghcr.io/hassio-addons/base:21.0.5

# hadolint ignore=DL3006
FROM ${BUILD_FROM}

# Set S6 verbosity level
ENV S6_VERBOSITY="1"

# renovate: datasource=repology depName=python3 packageName=alpine_3_24/python3 versioning=loose
ARG PYTHON3_VERSION="3.14.7-r1"
# renovate: datasource=repology depName=py3-yaml packageName=alpine_3_24/py3-yaml versioning=loose
ARG PY3_YAML_VERSION="6.0.3-r2"

# Setup base
# hadolint ignore=DL3018
RUN \
    apk add --no-cache \
        "python3=${PYTHON3_VERSION}" \
        "py3-yaml=${PY3_YAML_VERSION}" \
        jq \
    \
    && rm -fr \
        /tmp/* \
        /var/cache/apk/*

# Copy root filesystem
COPY rootfs /

# Copy the shipped migration plans
COPY plans /usr/share/app-assistant/plans

# Health check
HEALTHCHECK \
    --interval=30s \
    --timeout=10s \
    --retries=3 \
    CMD python3 /usr/lib/app-assistant/healthcheck.py || exit 1

# Build arguments
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version="${BUILD_VERSION}" \
    maintainer="Elcajon" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.vendor="Home Assistant App" \
    org.opencontainers.image.authors="Elcajon" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.url="https://github.com/elcajon/repository-edge" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.documentation="https://github.com/${BUILD_REPOSITORY}/blob/main/README.md" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
