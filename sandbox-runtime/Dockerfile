FROM python:3.11-slim-bookworm@sha256:2e32f7d302adc1c37428355c1e646897c0c53f4fd60b6a551245fb90ee129f91

ARG PYTORCH_VERSION=2.7.1

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin nova \
    && pip install --no-cache-dir ipykernel==6.29.5 \
    && pip install --no-cache-dir --only-binary=:all: \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==${PYTORCH_VERSION}" \
    && python -c "import torch; assert torch.__version__.split('+')[0] == '${PYTORCH_VERSION}'"

COPY nova_runtime.py /opt/nova-runtime/nova_runtime.py

USER 10001:10001
WORKDIR /workspace
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1

# Phase 2 warm readiness: the kernel is live, but contains no user state.
CMD ["python", "-m", "ipykernel", "-f", "/run/nova/kernel.json"]
