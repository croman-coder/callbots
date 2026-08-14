# Debian 12 (bookworm) sacó asterisk del archivo: `apt-get install asterisk`
# muere con "no installation candidate". Ubuntu 22.04 lo tiene en universe,
# versión 18.10, con los tres módulos de AudioSocket que necesita el callbot.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        asterisk \
        asterisk-modules \
        asterisk-config \
        gettext-base \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/asterisk/*.conf \
    # Ubuntu instala los módulos en la ruta multiarch. asterisk.conf apunta a
    # /usr/lib/asterisk/modules, así que se enlaza en vez de tocar la plantilla.
    && ln -sfn "$(dirname "$(dirname "$(find /usr/lib -type f -name res_ari.so | head -1)")")" /usr/lib/asterisk \
    && mkdir -p /var/run/asterisk \
    && chown asterisk:asterisk /var/run/asterisk

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Las plantillas van dentro de la imagen: el entrypoint las renderiza con
# envsubst a /etc/asterisk al arrancar. En desarrollo el compose monta
# ./docker/asterisk/etc encima y pisa estas, para editarlas sin rebuildear.
COPY etc/ /etc/asterisk-tpl/

EXPOSE 5060/udp 5060/tcp 8088/tcp

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD asterisk -rx "core show version" > /dev/null 2>&1 || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["asterisk", "-f", "-vvv"]
