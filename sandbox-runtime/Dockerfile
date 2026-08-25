FROM python:3.11-slim

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin nova \
    && pip install --no-cache-dir ipykernel==6.29.5

USER 10001:10001
WORKDIR /workspace
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1

# Phase 2 warm readiness: the kernel is live, but contains no user state.
CMD ["python", "-m", "ipykernel", "-f", "/run/nova/kernel.json"]
