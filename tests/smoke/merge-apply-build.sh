#!/bin/sh


SKTDIR=/usr/src/skt
TMPDIR=/tmp/skt
SKTRC=${TMPDIR}/skt-rc
SKTWORKDIR=${TMPDIR}/skt-workdir
SKTEXEC="python ${SKTDIR}/skt.py"

mkdir -p ${TMPDIR}

git config --global user.email "smoke@testing.com"
git config --global user.name "Smoke testing"

${SKTEXEC} --rc ${SKTRC} --state --workdir ${SKTWORKDIR} -vv \
    merge --baserepo git://git.kernel.org/pub/scm/linux/kernel/git/davem/net-next.git \
    --ref master

${SKTEXEC} --rc ${SKTRC} --state --workdir ${SKTWORKDIR} -vv \
    merge --baserepo git://git.kernel.org/pub/scm/linux/kernel/git/davem/net-next.git \
    --ref a870a02cc963de35452bbed932560ed69725c4f2 \
    --pw https://patchwork.ozlabs.org/patch/886637

${SKTEXEC} --rc ${SKTRC} --state --workdir ${SKTWORKDIR} -vv \
    build -c ${SKTDIR}/tests/smoke/kernel-minimal-config
