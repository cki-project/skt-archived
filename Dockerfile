FROM fedora:27

RUN dnf install -y python2 python2-junit_xml beaker-client \
	bison flex python2-mock dnf-plugins-core procps-ng
RUN dnf builddep -y kernel

# Use a default checkout of the kernel as our srcdir
RUN git clone git://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git /work && \
	chmod -R ugo+w /work

# Setup a valid git config in the container
RUN cd /work && \
	git config user.email "skt@example.com" && \
	git config user.name "Sonic Kernel Testing"

# Pull a default kernel config down for use in the container
# It's expected that this will be overriden either by mounting a
# new config file, or by adding one in a layered container
RUN mkdir -p /var/src/config && \
	curl -o /var/src/config/kernel-x86_64.config https://src.fedoraproject.org/rpms/kernel/raw/master/f/kernel-x86_64.config
ENV SKT_BASECONFIG=/var/src/config/kernel-x86_64.config

# Here's where our SKT work dir happens
WORKDIR /work
ENV SKT_WORKDIR=/work
ENV SKT_BUILDDIR=/work/build
RUN mkdir -p --mode=777 /work/build
VOLUME [ "/work/build" ]

# We don't pollute the container file system  tree with
# massive intermediate build output
COPY . /var/src/skt
RUN cd /var/src/skt && ls && pip install -v .

ENTRYPOINT [ "/usr/bin/skt" ]
