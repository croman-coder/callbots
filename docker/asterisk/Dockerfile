FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        asterisk \
        asterisk-modules \
        asterisk-config \
        gettext-base \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/asterisk/*.conf

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Las plantillas se montan acá; el entrypoint las renderiza a /etc/asterisk
VOLUME ["/etc/asterisk-tpl"]

EXPOSE 5060/udp 5060/tcp 8088/tcp

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD asterisk -rx "core show version" > /dev/null 2>&1 || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["asterisk", "-f", "-vvv"]
