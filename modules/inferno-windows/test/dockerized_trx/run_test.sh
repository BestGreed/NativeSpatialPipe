#!/bin/sh -e
pushd ../..

docker build -f Dockerfile.alpine-alsa -t inferno_aoip:alpine-alsa --build-arg BUILD_FLAGS= --build-arg BUILD_TYPE=debug .
docker build -f Dockerfile.fedora-alsa -t inferno_aoip:fedora-alsa --build-arg BUILD_FLAGS= --build-arg BUILD_TYPE=debug .
# FIXME: it will work only on x86/x86_64 host
docker buildx build --platform linux/386 -f Dockerfile.debian-alsa -t inferno_aoip:debian-alsa --build-arg BUILD_FLAGS= --build-arg BUILD_TYPE=debug .
docker build -f Dockerfile.alpine-i2pipe -t inferno_aoip:alpine-i2pipe --build-arg BUILD_FLAGS= --build-arg BUILD_TYPE=debug .

popd
#docker compose run --rm --build control_and_test
#docker compose down

docker compose down
docker compose up --build
# TODO: will wait infinitely, fix this
