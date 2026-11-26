# Immutable pins and their publication evidence are recorded in docs/plans/phase-6.md.
FROM node:26.13.1-bookworm@sha256:b7087bc4d973034b30854b378ba107f9fab622b47514cb7e2592e41b8bab8d49 AS web
WORKDIR /build
RUN case "$(dpkg --print-architecture)" in \
      amd64) arch=x64; checksum=881019018098a28c3a83468e6ca9a9aca9080463bbda62b82857ea22b63b8c874746401b37aeed70dd3381a3c4dfd50011d31cae42e12f012697e54cc5ff21f1 ;; \
      arm64) arch=arm64; checksum=d4c67719f14215c92ab8024fe9667df392eb77930fe58340eef3d91fbf56fb5415cf0979e5f54c76c00e7337469c5794e23a8cc04467935dbdff583dcd8c610f ;; \
      *) exit 1 ;; \
    esac && \
    curl -fsSL --retry 3 "https://registry.npmjs.org/@pnpm/exe.linux-${arch}/-/exe.linux-${arch}-12.22.0.tgz" -o /tmp/pnpm.tgz && \
    echo "$checksum  /tmp/pnpm.tgz" | sha512sum -c - && \
    tar -xzf /tmp/pnpm.tgz -C /usr/local/bin --strip-components=1 package/pnpm
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/tsconfig.json web/index.html ./
COPY web/src ./src
RUN pnpm run build

FROM python:3.14.10-bookworm@sha256:3fe825da0a80fa8e2cd79baadeb76b03c0ddf3d6cdbc8346778a0208b9d6038c
WORKDIR /opt/cerebro
RUN echo 'uv==0.13.9 --hash=sha256:d7f459fe4fd247d830351c75686d3996ace3c6f3f728eb0e85a30b7ada0cef19 --hash=sha256:ecb6028d79dc96fbeafffe5c8af62a7099487ebc05a701e76714eebcffd06375 --hash=sha256:5435c5ce14a56431208ca0996416d310f1598e8c2c8ba707688985a16bb82dbe' \
    | python -m pip install --no-cache-dir --only-binary=:all: --require-hashes --no-deps -r /dev/stdin
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
RUN uv sync --locked --no-dev --no-editable --no-cache --no-python-downloads && \
    groupadd --gid 10001 cerebro && useradd --uid 10001 --gid 10001 --create-home cerebro
COPY --from=web /build/dist ./web
ENV PATH="/opt/cerebro/.venv/bin:${PATH}" CEREBRO_PRIVATE_PORT=8765
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD ["python", "-c", "import http.client, os, sys; c=http.client.HTTPConnection('127.0.0.1', int(os.environ['CEREBRO_PRIVATE_PORT']), timeout=3); c.request('GET', '/mcp'); sys.exit(c.getresponse().status != 401)"]
CMD ["cerebro-mcp", "--container", "--web-dist", "/opt/cerebro/web"]
