FROM eclipse-mosquitto:2.0.22

USER root

COPY mosquitto.conf /mosquitto/config/mosquitto.conf
COPY docker-entrypoint.sh /usr/local/bin/kindcare-mosquitto-entrypoint
RUN chmod 0755 /usr/local/bin/kindcare-mosquitto-entrypoint

ENTRYPOINT ["/usr/local/bin/kindcare-mosquitto-entrypoint"]
