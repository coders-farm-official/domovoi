# The Linux box the hermetic harnesses of scripts/linux/tests/ run in, for
# scripts/dev/run-linux-harness.sh (which builds this once and tags it by a
# hash of this file, so an edit here means a rebuild and nothing else does).
#
# Ubuntu 26.04, the release the Linux server runs (docs/LINUX_HOST.md),
# pinned by the digest of its multi-arch index. Everything apt installs
# comes from Ubuntu's snapshot archive as it stood at SNAPSHOT, and the
# packages the harnesses call are pinned to the version they had there, so
# every build of this file installs the same bits, dependencies included.
# Its date(1) and friends are uutils, as on the server.
#
# What the harnesses need beyond the base image: git (every case builds
# throwaway repositories), ssh-keygen (the signed-update cases) and python3
# (the JSON checks, HARNESS_PYTHON). Everything else they call is in the
# base image or is a PATH shim the harness writes.
#
# To move it: take the new index digest (`docker buildx imagetools inspect
# ubuntu:26.04`) and/or a newer SNAPSHOT (a UTC time, YYYYMMDDTHHMMSSZ),
# then the version of each package at that snapshot (`apt-cache policy
# <pkg>` after `apt-get update --snapshot <SNAPSHOT>` in the new base), and
# run both harnesses through the runner before committing.

ARG BASE=ubuntu:26.04@sha256:f144425ff09be612d6d9ad965196e9cdc23dae1f42110a8a11a3e9a8198759f7

# The snapshot archive answers on https only and the base image has no CA
# bundle. This stage only fetches one, from the live archive; nothing from
# it but that file reaches the image, and apt checks every index and
# package it downloads against Ubuntu's archive keys whatever the transport.
FROM ${BASE} AS ca
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates

FROM ${BASE}
ARG SNAPSHOT=20261012T000000Z
COPY --from=ca /etc/ssl/certs/ca-certificates.crt /tmp/snapshot-ca.crt
RUN apt-get -o Acquire::https::CaInfo=/tmp/snapshot-ca.crt update --snapshot "$SNAPSHOT" \
 && DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::https::CaInfo=/tmp/snapshot-ca.crt \
      install -y --no-install-recommends --snapshot "$SNAPSHOT" \
      git=1:2.53.0-1ubuntu1 \
      openssh-client=1:10.2p1-2ubuntu3.7 \
      python3=3.14.3-0ubuntu2 \
 && rm -rf /var/lib/apt/lists/* /tmp/snapshot-ca.crt \
 && dpkg-query -W -f '${Package}=${Version}\n' >/etc/domovoi-harness-packages.txt

ENV LANG=C.UTF-8
# The image's own unprivileged user: the harnesses have cases (a venv the
# service user can't write, a state dir others can write) that only mean
# something when the caller is not root.
USER ubuntu
WORKDIR /home/ubuntu
