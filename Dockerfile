# syntax=docker/dockerfile:1
# Multi-architecture index: all four targets use the same Ubuntu release snapshot.
ARG UBUNTU_IMAGE=ubuntu:26.04@sha256:6a06111c3b0a8600feadb270aec771dc239bf407b25625f93f44ab00e11f4d3c
FROM ${UBUNTU_IMAGE}

# QEMU reads this during RUN as well: resolute requires POWER9 / RVA23 userspace.
# The host launcher only supplies it when the target is emulated.
ARG QEMU_CPU
ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential ca-certificates clang lld cmake ninja-build file nodejs \
        python3 python3-venv python3-yaml python3-setuptools \
    && rm -rf /var/lib/apt/lists/*

ARG EQUILANE_GIT_COMMIT=unknown
ENV EQUILANE_GIT_COMMIT=${EQUILANE_GIT_COMMIT}

WORKDIR /opt/equilane
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/python -m pip install --no-build-isolation --no-deps .
ENV PATH=/opt/venv/bin:$PATH
COPY CMakeLists.txt equilane.toml ioitf.toml ./
COPY adapters ./adapters
COPY include ./include
COPY contracts ./contracts
COPY vendor ./vendor
COPY tools ./tools
COPY 10_official_suite ./10_official_suite
COPY tests ./tests
COPY docker ./docker
COPY documentation ./documentation
CMD ["equilane", "--help"]
