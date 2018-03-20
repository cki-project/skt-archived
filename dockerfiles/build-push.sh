#!/bin/sh

set -x

docker pull ${DOCKER_USER}/skt-dev
docker run -v ${PWD}/dockerfiles/skt-dev.dockerfile:/tmp/skt-dev.dockerfile \
    ${DOCKER_USER}/skt-dev \
    bash -c "diff /skt-dev.dockerfile /tmp/skt-dev.dockerfile" \
    && exit 0
docker build -t ${DOCKER_USER}/skt-dev -f ./dockerfiles/skt-dev.dockerfile .
docker images
docker login -u="$DOCKER_USER" -p="$DOCKER_PASS"
docker push ${DOCKER_USER}/skt-dev
