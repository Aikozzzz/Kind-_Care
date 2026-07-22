#!/bin/sh
set -eu

: "${MQTT_USERNAME:?MQTT_USERNAME is required}"
: "${MQTT_PASSWORD:?MQTT_PASSWORD is required}"

carriage_return="$(printf '\r')"
line_feed='
'

reject_line_breaks() {
    credential_name="$1"
    credential_value="$2"
    case "$credential_value" in
        *"$carriage_return"*|*"$line_feed"*)
            echo "$credential_name must not contain CR or LF" >&2
            exit 1
            ;;
    esac
}

reject_line_breaks MQTT_USERNAME "$MQTT_USERNAME"
reject_line_breaks MQTT_PASSWORD "$MQTT_PASSWORD"

case "$MQTT_USERNAME" in
    *:*) echo "MQTT_USERNAME must not contain a colon" >&2; exit 1 ;;
esac

umask 077
mkdir -p /mosquitto/data
password_tmp="/mosquitto/data/.passwords.$$"
trap 'rm -f "$password_tmp"' EXIT INT TERM
printf '%s:%s\n' "$MQTT_USERNAME" "$MQTT_PASSWORD" > "$password_tmp"
mosquitto_passwd -U "$password_tmp"
mv "$password_tmp" /mosquitto/data/passwords
trap - EXIT INT TERM
chown -R mosquitto:mosquitto /mosquitto/data

exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf
