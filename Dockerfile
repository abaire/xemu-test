FROM xboxdev/nxdk:latest AS nxdk-base


#
# Build test data
#
FROM nxdk-base AS test-xbe-data
RUN mkdir /data

COPY test-xbe /test-xbe
RUN mkdir /data/TestXBE
RUN /usr/src/nxdk/docker_entry.sh make -C /test-xbe
RUN cp /test-xbe/tester.iso /data/TestXBE/

#
# Curl/git enabled base
#
FROM nxdk-base AS github-curl-base

RUN apk add --upgrade --no-cache curl libcurl git


#
# Fetch nxdk_low_level_nv2a_tests
#
FROM github-curl-base AS low-level-nv2a-data

ARG NV2A_TAG="v2026-10-11_05-34-09-051521338"

RUN mkdir -p /data/TestNxdkLowLevelNV2ATests

WORKDIR /work

RUN set -e; \
    cat <<'EOF' | \
    while read -r test; do \
      [ -n "$test" ] && \
      curl -fL "https://github.com/abaire/nxdk_low_level_nv2a_tests/releases/download/${NV2A_TAG}/${test}.iso" \
        -o "/data/TestNxdkLowLevelNV2ATests/${test}.iso";\
    done
clock_test_xiso
m2mf_test_xiso
pfifo_cache1_test_xiso
ptimer_alarm_test_xiso
xemu_guest_info_test_xiso
EOF


#
# Fetch nxdk_low_level_cpu_tests
#
FROM github-curl-base AS low-level-cpu-data

ARG CPU_TAG="v2026-10-12_04-15-41-338891049"

WORKDIR /work

RUN mkdir -p /data/TestNxdkLowLevelCPUTests

RUN set -e; \
    cat <<'EOF' | \
    while read -r test; do \
      [ -n "$test" ] && \
      curl -fL "https://github.com/abaire/nxdk_low_level_cpu_tests/releases/download/${CPU_TAG}/${test}.iso" \
        -o "/data/TestNxdkLowLevelCPUTests/${test}.iso";\
    done
fpu_test_xiso
sse_test_xiso
testfloat_test_xiso
EOF


FROM ubuntu:25.10 AS ubuntu-base
RUN set -xe; \
    apt-get -qy update \
    && apt-get -qy install \
        python3-pip \
        python3-venv \
    ;

#
# Build xemutest module
#
FROM ubuntu-base AS build-xemutest

RUN apt-get -qy install \
        cmake \
        ;

WORKDIR /build
COPY . /work/xemu-test
RUN python3 -m venv venv \
    && . venv/bin/activate \
    && pip install /work/xemu-test \
    ;


#
# Build base test container image
#
FROM ubuntu-base AS run-container-base
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -qy install \
        python3-pip \
        xvfb \
        x11-utils \
        x11vnc \
        xinit \
        ffmpeg \
        i3 \
        qemu-utils \
        libc6 \
        libepoxy0 \
        libgcc-s1 \
        libglib2.0-0 \
        libgtk-3-0 \
        libpcap0.8 \
        libpixman-1-0 \
        libpulse0 \
        libsamplerate0 \
        libsdl2-2.0-0 \
        libssl-dev \
        libstdc++6 \
        zlib1g \
        ;

# Combine test data
FROM scratch AS data
COPY --from=test-xbe-data /data /data
COPY --from=low-level-nv2a-data /data /data
COPY --from=low-level-cpu-data /data /data

#
# Build final test container
#
FROM run-container-base AS test-container
ENV DEBIAN_FRONTEND=noninteractive
ENV SDL_AUDIODRIVER=dummy

# VNC port for debugging

EXPOSE 5900

WORKDIR /work

COPY scripts/docker_entry.sh /docker_entry.sh
COPY ./scripts /work/xemu-test/scripts/
COPY --from=data /data /work/xemu-test/data
COPY --from=build-xemutest /build/venv /venv

ENTRYPOINT ["/docker_entry.sh"]

CMD ["/venv/bin/python3", "-m", "xemutest", "--data", "/work/xemu-test/data", "xemu", "/work/private", "/work/results"]
