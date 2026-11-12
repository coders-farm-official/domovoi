# The network-name multicast test's image (scripts/dev/run-lan-name-test.sh):
# the Linux harness base (linux-harness.Dockerfile: Ubuntu 26.04 by digest,
# packages from a fixed snapshot) plus Avahi, so one case can check that
# Domovoi's responder binds UDP 5353 beside avahi-daemon and treats the name
# Avahi publishes for this box as its own. Avahi is a test tool here, never
# shipped or imported (LGPL; docs/CONTRIBUTING.md). Runs as root: avahi-daemon
# starts as root and drops to its own user. The containers sit on a private
# Docker bridge of their own; mDNS (224.0.0.251, link-local) never leaves it.
ARG BASE=ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7

FROM ${BASE} AS ca
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates

FROM ${BASE}
ARG SNAPSHOT=20261012T000000Z
COPY --from=ca /etc/ssl/certs/ca-certificates.crt /tmp/snapshot-ca.crt
RUN apt-get -o Acquire::https::CaInfo=/tmp/snapshot-ca.crt update --snapshot "$SNAPSHOT" \
 && DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::https::CaInfo=/tmp/snapshot-ca.crt \
      install -y --no-install-recommends --snapshot "$SNAPSHOT" \
      python3=3.14.3-0ubuntu2 \
      avahi-daemon=0.8-18ubuntu1.1 \
      dbus-daemon=1.16.2-2ubuntu4 \
 && rm -rf /var/lib/apt/lists/* /tmp/snapshot-ca.crt \
 && dpkg-query -W -f '${Package}=${Version}\n' >/etc/domovoi-harness-packages.txt

ENV LANG=C.UTF-8
WORKDIR /root
