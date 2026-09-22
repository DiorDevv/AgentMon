#!/bin/sh
# Sertifikat berilmagan bo'lsa — o'z-o'zidan imzolangan sertifikat yaratadi (bir marta, ./certs ga saqlanadi).
# Real muhitda korporativ CA sertifikatini ./certs/tls.crt va ./certs/tls.key sifatida qo'ying.
set -eu
DIR=/etc/nginx/certs
if [ -s "$DIR/tls.crt" ] && [ -s "$DIR/tls.key" ]; then
    exit 0
fi
CN="${WEB_TLS_CN:-agentmon}"
SAN="${WEB_TLS_SAN:-DNS:$CN,DNS:localhost}"
echo "agentmon: $DIR/tls.crt topilmadi — o'z-o'zidan imzolangan sertifikat yaratilmoqda (CN=$CN, $SAN)"
mkdir -p "$DIR"
umask 077
openssl req -x509 -newkey rsa:2048 -nodes -days 825 -sha256 \
    -subj "/CN=$CN/O=AgentMon" -addext "subjectAltName=$SAN" \
    -keyout "$DIR/tls.key" -out "$DIR/tls.crt" 2>/dev/null
chmod 644 "$DIR/tls.crt"
