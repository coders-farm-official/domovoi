# The Linux box the printing harness (scripts/linux/tests/test-printing.sh)
# runs in, for scripts/dev/run-linux-harness.sh: the same pinned Ubuntu
# 26.04 and package snapshot as linux-harness.Dockerfile, plus
# cups-ipp-utils for ippeveprinter, a real IPP Everywhere printer
# simulator (Apache-2.0, CUPS 2.4), used for TESTS ONLY and never shipped.
#
# ippeveprinter links libavahi-client (LGPL-2.1) as a shared library, but
# the harness runs it with `-r off` (no DNS-SD) and no Avahi daemon exists
# in the container: nothing of Avahi runs, and nothing of it reaches a
# Domovoi install (plan Q21: no Avahi or AirSane even in tests). The
# Avahi client library still wants a D-Bus to talk to before ippeveprinter
# starts, so the harness runs a private, unprivileged dbus-daemon
# (AFL-2.1 or GPL-2.0, test container only) that nothing answers on.
#
# To move it, follow the note in linux-harness.Dockerfile, and take the
# cups-ipp-utils version from `apt-cache policy cups-ipp-utils` at the new
# snapshot.

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
      cups-ipp-utils=2.4.16-1ubuntu1.3 \
      dbus-daemon=1.16.2-2ubuntu4 \
 && rm -rf /var/lib/apt/lists/* /tmp/snapshot-ca.crt \
 && dpkg-query -W -f '${Package}=${Version}\n' >/etc/domovoi-harness-packages.txt

ENV LANG=C.UTF-8
USER ubuntu
WORKDIR /home/ubuntu
