# Pre-baked Db2 benchmark image (CIW-8/CIW-9)
#
# Derives from the official Db2 Community Edition image and pre-bakes:
#   1. The TESTDB creation hook (/var/custom/01_create_testdb.sh)
#   2. The log expansion config (LOGFILSIZ 4096 LOGPRIMARY 30 LOGSECOND 64)
#
# ── LAYER PERSISTENCE CHECK — CIW-8 BLOCKER ────────────────────────────────
# Db2 writes database files to a path that may be declared as a Docker VOLUME
# in the upstream image. If so, filesystem changes at that path are NOT
# committed into the layer — they are silently discarded on `docker commit`.
# This means the TESTDB creation step may not actually persist.
#
# BEFORE pushing this image to GHCR or using it in CI, verify:
#
#   1. Build this image:
#      docker build -t db2-baked-test .github/db2-bench/
#
#   2. Start a container and wait for full init (~5 min):
#      docker run -d --name db2-verify --privileged -e LICENSE=accept db2-baked-test
#      docker logs -f db2-verify  # wait for "Setup has completed"
#
#   3. Confirm TESTDB exists in the running container:
#      docker exec db2-verify su - db2inst1 -c "db2 connect to TESTDB"
#      # Expected: "Database Connection Information" block — success
#
#   4. Commit the container to a new image:
#      docker commit db2-verify db2-test-layer
#      docker rm -f db2-verify
#
#   5. Run the committed image and test again:
#      docker run -d --name db2-layer-test --privileged -e LICENSE=accept db2-test-layer
#      docker logs -f db2-layer-test  # wait for init
#      docker exec db2-layer-test su - db2inst1 -c "db2 connect to TESTDB"
#
#   If step 5 succeeds (TESTDB exists without re-creation): approach works → go.
#   If step 5 fails (TESTDB does not exist): the database was written to a
#   VOLUME path and this approach must be abandoned. Close CIW-9 with that
#   finding and fall back to CIW-10/CIW-11's batching approach.
#
# ── LICENSING NOTE ──────────────────────────────────────────────────────────
# The IBM Db2 Community Edition license permits free use for development and
# testing. Republishing a derived image must comply with IBM license terms.
# Confirm before pushing to a public registry.
#
# ── UPSTREAM ────────────────────────────────────────────────────────────────
# Base:      icr.io/db2_community/db2:12.1.5.0
# Published: ghcr.io/<org>/db2-bench:12.1.5.0
# Rebuild:   .github/workflows/db2-image.yml (weekly + path trigger)

ARG DB2_BASE_VERSION=12.1.5.0
FROM icr.io/db2_community/db2:${DB2_BASE_VERSION}

# Pre-bake the TESTDB creation hook into /var/custom.
# Note: this only works if /var/custom is NOT declared as a VOLUME in the
# base image. If it is, use a custom entrypoint wrapper instead.
COPY 01_create_testdb.sh /var/custom/01_create_testdb.sh
RUN chmod +x /var/custom/01_create_testdb.sh
