# Mosquitto broker setup (MQTT over TLS)

`certs/` and `passwd` are NOT in the repo (see `.gitignore`). Create them once:

```
# 1. certificates (a local CA + server certificate; use your real hostname for CN)
mkdir certs && cd certs
openssl req -new -x509 -days 365 -nodes -newkey rsa:2048 -keyout ca.key -out ca.crt -subj "/CN=JalBhoomi-CA"
openssl req -new -nodes -newkey rsa:2048 -keyout server.key -out server.csr -subj "/CN=mosquitto"
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 365
cd ..

# 2. one user per device, plus one for the backend
docker run --rm -it -v "$PWD":/work eclipse-mosquitto:2 mosquitto_passwd -c /work/passwd backend
docker run --rm -it -v "$PWD":/work eclipse-mosquitto:2 mosquitto_passwd /work/passwd jalbhoomi-esp32-01
```

The ESP32 firmware connects with its own user and publishes to
`jalbhoomi/<DEVICE_ID>/summary`. For anything beyond a bench test, load
`ca.crt` into the firmware with `setCACert()` instead of `setInsecure()`.
