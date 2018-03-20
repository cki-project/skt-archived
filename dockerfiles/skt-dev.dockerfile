from fedora

run dnf install python2 python2-junit_xml beaker-client dnf-plugins-core -y

run dnf builddep kernel -y

run dnf install bison flex -y

run dnf install python2-mock python2-pycodestyle python2-pylint -y

copy ./dockerfiles/skt-dev.dockerfile /

# vim: set syntax=dockerfile:
