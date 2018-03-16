skt - sonic kernel testing
==========================

Skt is a tool for automatically fetching, building, and testing kernel
patches published on Patchwork instances.

Dependencies
------------

Install dependencies needed for running skt like this:

    $ sudo dnf install python2 python2-junit_xml beaker-client

Dependencies needed to build kernels:

    $ sudo dnf builddep kernel-`uname -r`
    $ sudo dnf install bison flex

Extra dependencies needed for running the testsuite:

    $ sudo dnf install python2-mock

Run tests
---------

For running all tests write down:

    $ python -m unittest discover tests

For running some specific tests you can do this as following:

    $ python -m unittest tests.test_publisher

Usage
-----

A simple workflow would be checking out a base repository,
applying patches from Patchwork, and building the kernel.

To checkout a kernel tree run:

    $ skt.py --workdir <WORKDIR> -vv \
             merge --baserepo <REPO_URL> --ref <REPO_REF>

Here `<WORKDIR>` would be the directory to clone and checkout the kernel repo
to, `<REPO_URL>` would be the source kernel Git repo URL, and `<REPO_REF>`
would be the refernce to checkout.

E.g. to checkout "master" branch of the "net-next" repo:

    $ skt.py --workdir skt-workdir -vv \
             merge --baserepo git://git.kernel.org/pub/scm/linux/kernel/git/davem/net-next.git \
                   --ref master

To apply a patch from Patchwork run:

    $ skt.py --workdir <WORKDIR> -vv \
             merge --baserepo <REPO_URL> \
                   --ref <REPO_REF> \
                   --pw <PATCHWORK_PATCH_URL>

Here, `<REPO_REF>` would be the reference to checkout, and to apply the patch
on top of, and `<PATCHWORK_PATCH_URL>` would be a URL pointing to a patch on a
Patchwork instance.

E.g. to apply a particular patch to a particular, known-good commit from the
"net-next" repo, run:

    $ skt.py --workdir skt-workdir -vv \
             merge --baserepo git://git.kernel.org/pub/scm/linux/kernel/git/davem/net-next.git \
                   --ref a870a02cc963de35452bbed932560ed69725c4f2 \
                   --pw https://patchwork.ozlabs.org/patch/886637

And to build the kernel run:

    $ skt.py --workdir skt-workdir -vv \
             build -c `<CONFIG_FILE>`

Where `<CONFIG_FILE>` would be the kernel configuration file to build the
kernel with. The configuration will be applied with `make olddefconfig`, by
default.

E.g. to build with the current system's config file run:

    $ skt.py --workdir skt-workdir -vv \
             build -c /boot/config-`uname -r`

All the above commands use the `-vv` option to increase verbosity of the
command's output, so it's easier to debug problems. Remove the option for
quiter, shorter output.

You can make skt output junit-compatible results by adding a `--junit
<JUNIT_DIR>` option to any of the above commands. The results will be written
to the `<JUNIT_DIR>` directory.

License
-------
skt is distributed under GPLv2 license.

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
