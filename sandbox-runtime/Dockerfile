FROM python:3.11-slim-bookworm@sha256:2e32f7d302adc1c37428355c1e646897c0c53f4fd60b6a551245fb90ee129f91

ARG PYTORCH_VERSION=2.7.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /opt/nova-runtime/requirements.lock
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin nova \
    && pip install --no-cache-dir --require-hashes -r /opt/nova-runtime/requirements.lock \
    && pip install --no-cache-dir --only-binary=:all: \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==${PYTORCH_VERSION}" \
    && pip check \
    && python -c "import numpy as np, torch; assert torch.__version__.split('+')[0] == '${PYTORCH_VERSION}'; assert torch.from_numpy(np.ones(1)).numpy()[0] == 1"

COPY nova_runtime.py /opt/nova-runtime/nova_runtime.py
COPY verify_profile.py matplotlibrc /opt/nova-runtime/

USER 10001:10001
WORKDIR /workspace
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg MPLCONFIGDIR=/tmp/matplotlib MATPLOTLIBRC=/opt/nova-runtime \
    HF_HOME=/tmp/huggingface HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1

# Phase 2 warm readiness: the kernel is live, but contains no user state.
CMD ["python", "-m", "ipykernel", "-f", "/run/nova/kernel.json"]
