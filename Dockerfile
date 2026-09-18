# =============================================================================
# Stage 1 — Build React frontend
# =============================================================================
FROM node:22-alpine AS frontend-builder

# Registry mirrors are build-time configurable: npm ci in China is unreliable
# against registry.npmjs.org (dropped connections / "Exit handler never
# called"), so deployments can pass NPM_REGISTRY=https://registry.npmmirror.com.
# The lockfile pins per-package resolved hosts (registry.npmjs.org and an old
# msh.team mirror), which bypass the --registry flag; replace-registry-host
# forces every resolved host through the configured registry.
ARG NPM_REGISTRY=https://registry.npmjs.org

ENV npm_config_registry=${NPM_REGISTRY} \
    npm_config_replace_registry_host=always \
    npm_config_fetch_retries=5 \
    npm_config_fetch_timeout=180000 \
    npm_config_fetch_retry_mintimeout=20000

WORKDIR /src/app
COPY app/package.json app/package-lock.json ./
RUN npm ci

COPY app/ ./
RUN npm run build

# =============================================================================
# Stage 2 — Install Python dependencies
# =============================================================================
FROM python:3.12-slim AS python-deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The default image intentionally does not install the optional local NLI
# stack (transformers + PyTorch), Docling, or PaperQA2. Those integrations
# are available through explicit extras when a dedicated ML image is needed.
# For the default PDF path, PyMuPDF is sufficient.
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

# Empty = standard image. The enhanced CI profile passes ``full`` here.
ARG INSTALL_EXTRAS=
# Only used when an extra needs torch (nli/docling). The standard image never
# evaluates this URL or downloads PyTorch.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu/

WORKDIR /app

COPY pyproject.toml README.md ./
COPY radar ./radar
# Demo case + fixture scan adapter data (fixture_case_dir defaults to this path).
COPY tests/fixtures/golden_case ./tests/fixtures/golden_case
# Python 3.12 has a prebuilt manylinux wheel for chroma-hnswlib, so neither
# profile needs a compiler toolchain or a long source build. Install torch
# from the CPU-only wheel index first only for the enhanced ML/PDF profile;
# the standard image remains torch-free.
RUN if [ -n "${INSTALL_EXTRAS}" ]; then \
      case ",${INSTALL_EXTRAS}," in \
        *,nli,*|*,docling,*|*,full,*) \
          pip install --no-cache-dir --find-links "${TORCH_INDEX_URL}" \
            "torch>=2.2.2,<3.0.0" ;; \
      esac; \
      pip install --no-cache-dir --only-binary=chroma-hnswlib \
        ".[${INSTALL_EXTRAS}]"; \
    else \
      pip install --no-cache-dir --only-binary=chroma-hnswlib .; \
    fi

# Copy built frontend into static directory
COPY --from=frontend-builder /src/app/dist /app/static

# =============================================================================
# Stage 3 — Final runtime image
# =============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy everything we installed and built from stage 2
COPY --from=python-deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=python-deps /usr/local/bin /usr/local/bin
COPY --from=python-deps /app /app

EXPOSE 8501

CMD ["uvicorn", "radar.api:app", "--host", "0.0.0.0", "--port", "8501"]
