# The pinned Linux environment the Android app's release APKs are built in
# (android/README.md, "Reproducible builds"). The same image builds them on
# the owner's PC (android/scripts/release.ps1 and release.sh, through Docker)
# and twice in CI (.github/workflows/ci.yml, android-repro-build), which
# checks the two builds are byte for byte the same; F-Droid's own build of a
# release must match it too.
#
# Every input is pinned, and what it installed is recorded in
# /etc/domovoi-build-env.txt, which CI holds to android/build-env/BUILD-ENV.txt:
#   - the base image by digest (ubuntu:24.04, the index published 2026-09-18;
#     android/native/sherpa-onnx/build.sh uses the same one);
#   - apt packages at exact versions from one Ubuntu snapshot;
#   - the JDK: Temurin 21.0.13+9 by SHA-256 (Adoptium's published checksum).
#     21, not 17: LiteRT-LM's classes are Java 21 class files, which the unit
#     tests load (ci.yml's android job). The app still compiles to Java 17
#     bytecode (android/app/build.gradle.kts compileOptions and jvmTarget).
#     Ubuntu's own OpenJDK 21 is installed beside it for the second CI build,
#     which proves the JDK's vendor and patch don't change the APK;
#   - the Android SDK packages the build reads, by SHA-256 (the SHA-1s match
#     the ones Google's repository2-3.xml lists for them): platform 36 r02
#     and build-tools 35.0.0 (android/app/build.gradle.kts buildToolsVersion).
#     No NDK: the build never strips or builds native code
#     (packaging.jniLibs.keepDebugSymbols), and with no licence files here a
#     missing package fails the build instead of being downloaded.
# The image is named by the git tree of android/build-env (build.sh), so a
# change to anything here builds a new one.
# Gradle itself comes from the wrapper (gradle-wrapper.properties pins its
# SHA-256) and every Maven artifact from gradle/verification-metadata.xml.
# To move a pin: change it here and in BUILD-ENV.txt, in one commit.
FROM ubuntu@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55

ENV DEBIAN_FRONTEND=noninteractive

# The snapshot service is HTTPS-only, so the CA bundle comes first from the
# image's own archive; it only decides which TLS certificates are trusted.
RUN set -eu; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends ca-certificates >/dev/null; \
    sed -i -E "s#http://(archive|security)\.ubuntu\.com/ubuntu/?#https://snapshot.ubuntu.com/ubuntu/20261030T000000Z/#" \
      /etc/apt/sources.list.d/ubuntu.sources; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
      curl=8.5.0-2ubuntu10.16 \
      unzip=6.0-28ubuntu4.1 \
      git=1:2.43.0-1ubuntu7.4 \
      locales=2.39-0ubuntu8.9 \
      tzdata=2026e-0ubuntu0.24.04.1 \
      openjdk-21-jdk-headless=21.0.12.1+1-1~24.04.4 \
      apksigcopier=1.1.1-1 \
      >/dev/null; \
    printf 'C.UTF-8 UTF-8\nen_US.UTF-8 UTF-8\ntr_TR.UTF-8 UTF-8\n' >/etc/locale.gen; \
    locale-gen >/dev/null; \
    rm -rf /var/lib/apt/lists/*

# Fetch $1 to $2 and hold it to the SHA-256 $3.
RUN printf '%s\n' '#!/bin/sh' 'set -eu' \
      'curl -fsSL --retry 3 -o "$2" "$1"' \
      'echo "$3  $2" | sha256sum -c --quiet -' >/usr/local/bin/fetch-pinned; \
    chmod 755 /usr/local/bin/fetch-pinned

RUN set -eu; \
    fetch-pinned https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/OpenJDK21U-jdk_x64_linux_hotspot_21.0.13_9.tar.gz \
      /tmp/jdk.tar.gz d7f5d5cf865a2902b70ff549b97586a7d3f234e016f041ab447ea740c7d66d1c; \
    mkdir -p /opt/temurin-21; \
    tar -xzf /tmp/jdk.tar.gz -C /opt/temurin-21 --strip-components=1; \
    rm /tmp/jdk.tar.gz

ENV ANDROID_HOME=/opt/android-sdk
RUN set -eu; \
    mkdir -p "$ANDROID_HOME/platforms" "$ANDROID_HOME/build-tools"; \
    fetch-pinned https://dl.google.com/android/repository/platform-36_r02.zip \
      /tmp/platform.zip 37607369a28c5b640b3a7998868d45898ebcb777565a0e85f9acf36f29631d2e; \
    unzip -q /tmp/platform.zip -d "$ANDROID_HOME/platforms"; \
    fetch-pinned https://dl.google.com/android/repository/build-tools_r35_linux.zip \
      /tmp/build-tools.zip bd3a4966912eb8b30ed0d00b0cda6b6543b949d5ffe00bea54c04c81e1561d88; \
    unzip -q /tmp/build-tools.zip -d /tmp/bt; \
    mv /tmp/bt/android-15 "$ANDROID_HOME/build-tools/35.0.0"; \
    rm -rf /tmp/platform.zip /tmp/build-tools.zip /tmp/bt

ENV JAVA_HOME=/opt/temurin-21 \
    PATH=/opt/temurin-21/bin:/opt/android-sdk/build-tools/35.0.0:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    TZ=UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# What is installed, for BUILD-ENV.txt (no dates, no build-machine details).
RUN set -eu; \
    { \
      echo "base ubuntu@sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55"; \
      echo "apt snapshot 20261030T000000Z"; \
      dpkg-query -W -f='apt ${Package} ${Version}\n' curl unzip git locales tzdata openjdk-21-jdk-headless apksigcopier; \
      echo "jdk $(/opt/temurin-21/bin/java -XshowSettings:properties -version 2>&1 | sed -n 's/^ *java.vendor.version = //p') $(/opt/temurin-21/bin/java -XshowSettings:properties -version 2>&1 | sed -n 's/^ *java.runtime.version = //p') (JAVA_HOME /opt/temurin-21)"; \
      echo "jdk-alt $(/usr/lib/jvm/java-21-openjdk-amd64/bin/java -XshowSettings:properties -version 2>&1 | sed -n 's/^ *java.vm.vendor = //p') $(/usr/lib/jvm/java-21-openjdk-amd64/bin/java -XshowSettings:properties -version 2>&1 | sed -n 's/^ *java.runtime.version = //p') (/usr/lib/jvm/java-21-openjdk-amd64)"; \
      echo "sdk platforms;android-36 revision $(sed -n 's/^Pkg.Revision=//p' "$ANDROID_HOME/platforms/android-36/source.properties")"; \
      echo "sdk build-tools;35.0.0 revision $(sed -n 's/^Pkg.Revision=//p' "$ANDROID_HOME/build-tools/35.0.0/source.properties")"; \
    } >/etc/domovoi-build-env.txt; \
    cat /etc/domovoi-build-env.txt

# The entry build.sh and build.ps1 run: a clean checkout of one commit from
# the repository mounted at /git, built by that commit's build-apks.sh.
COPY --chmod=755 build-commit.sh /usr/local/bin/domovoi-build-commit
