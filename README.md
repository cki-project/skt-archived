skt - sonic kernel testing
==========================

Skt is a tool for automatically fetching, building, and testing kernel
patches published on Patchwork instances.

Dependencies
------------

Install dependencies needed for running skt like this:

    $ sudo dnf install python2 python2-junit_xml beaker-client

Dependencies needed to build kernels:

    $ sudo dnf install bison flex dnf-plugins-core
    $ sudo dnf builddep kernel-`uname -r`

Extra dependencies needed for running the testsuite:

    $ sudo dnf install python2-mock

Run tests
---------

For running all tests write down:

    $ python -m unittest discover tests

For running some specific tests you can do this as following:

    $ python -m unittest tests.test_publisher


Installation
------------

Install `skt` directly from git:

    $ pip install git+https://github.com/RH-FMK/skt

Test the `skt` executable by printing the help text:

    $ skt -h

Usage
-----

The `skt` tool implements several "commands", and each of those accepts its
own command-line options and arguments. However there are also several
"global" command-line options, shared by all the commands. To get a summary of
the global options and available commands, run `skt.py --help`. To get a
summary of particular command's options and arguments, run `skt.py <COMMAND>
--help`, where `<COMMAND>` would be the command of interest.

Most of command-line options can also be read by `skt` from its configuration
file, which is `~/.sktrc` by default, but can also be specified using the
global `--rc` command-line option. However, there are some command-line
options which cannot be stored in the configuration file, and there are some
options read from the configuration file by some `skt` commands, which cannot
be passed via the command line. Some of the latter are required for operation.

Most `skt` commands can write their state to the configuration file as they
work, so that the other commands can take the workflow task over from them.
Some commands can receive that state from the command line, via options, but
some require some information stored in the configuration file. For this
reason, to support a complete workflow, it is necessary to always make the
commands transfer their state via the configuration file. That can be done by
passing the global `--state` option with every command.

To separate the actual configuration from the specific workflow's state, and
to prevent separate tasks from interfering with each other, you can store your
configuration in a separate (e.g. read-only) file, copy it to a new file each
time you want to do something, then discard the file after the task is
complete. Note that reusing a configuration file with state added can break
some commands in unexpected ways. That includes repeating a previous command
after the next command in the workflow has already ran.

The following commands are supported by `skt`:

* `merge`
    - Fetch a kernel repository, checkout particular references, and
      optionally apply patches from patchwork instances.
* `build`
    - Build the kernel with specified configuration and put it into a tarball.
      This command expects `merge` command to have completed succesfully.
* `publish`
    - Publish (copy) the kernel tarball, configuration, and build information
      to the specified location, generating their resulting URLs, using the
      specified "publisher". Only "cp" and "scp" pusblishers are supported at
      the moment. This command expects `build` command to have completed
      succesfully.
* `run`
    - Run tests on a built kernel using the specified "runner". Only
      "Beaker" runner is currently supported. This command expects `publish`
      command to have completed succesfully.
* `report`
    - Report build and/or test results using the specified "reporter".
      Currently results can be reported by e-mail or printed to stdout. This
      command expects `run` command to have completed.
* `cleanup`
    - Remove the build information file, kernel tarball. Remove state
      information from the configuration file, if saving state was enabled
      with the global `--state` option, and remove the whole working directory,
      if the global `--wipe` option was specified.
* `all`
    - Run the following commands in order: `merge`, `build`, `publish`, `run`,
      `report` (if `--wait` option was specified), and `cleanup`.
* `bisect`
    - Bisect Git history between a known bad and a known good commit
      (defaulting to "master"), running tests to locate the offending commit.

The following is a walk through the process of checking out a kernel commit,
applying a patch from Patchwork, building the kernel, running the tests,
reporting the results, and cleaning up.

All the following commands use the `-vv` option to increase verbosity of the
command's output, so it's easier to debug problems. Remove the option for
quieter, shorter output.

You can make `skt` output junit-compatible results by adding a `--junit
<JUNIT_DIR>` option to any of the following commands. The results will be
written to the `<JUNIT_DIR>` directory.

### Merge

To checkout a kernel tree run:

    $ skt --rc <SKTRC> --state --workdir <WORKDIR> -vv \
             merge --baserepo <REPO_URL> --ref <REPO_REF>

Here `<SKTRC>` would be the configuration file to retrieve the configuration
and the state from, and store the updated state in. `<WORKDIR>` would be the
directory to clone and checkout the kernel repo to, `<REPO_URL>` would be the
source kernel Git repo URL, and `<REPO_REF>` would be the reference to
checkout.

E.g. to checkout "master" branch of the "net-next" repo:

    $ skt --rc skt-rc --state --workdir skt-workdir -vv \
             merge --baserepo git://git.kernel.org/pub/scm/linux/kernel/git/davem/net-next.git \
                   --ref master

To apply a patch from Patchwork run:

    $ skt --rc <SKTRC> --state --workdir <WORKDIR> -vv \
             merge --baserepo <REPO_URL> \
                   --ref <REPO_REF> \
                   --pw <PATCHWORK_PATCH_URL>

Here, `<REPO_REF>` would be the reference to checkout, and to apply the patch
on top of, and `<PATCHWORK_PATCH_URL>` would be the URL pointing to a patch on
a Patchwork instance.

E.g. to apply a particular patch to a particular, known-good commit from the
"net-next" repo, run:

    $ skt --rc <SKTRC> --state --workdir skt-workdir -vv \
             merge --baserepo git://git.kernel.org/pub/scm/linux/kernel/git/davem/net-next.git \
                   --ref a870a02cc963de35452bbed932560ed69725c4f2 \
                   --pw https://patchwork.ozlabs.org/patch/886637

### Build

And to build the kernel run:

    $ skt --rc <SKTRC> --state --workdir skt-workdir -vv \
             build -c `<CONFIG_FILE>`

Where `<CONFIG_FILE>` would be the kernel configuration file to build the
kernel with. The configuration will be applied with `make olddefconfig`, by
default.

E.g. to build with the current system's config file run:

    $ skt --rc <SKTRC> --state --workdir skt-workdir -vv \
             build -c /boot/config-`uname -r`

**NOTE:** Kernels are built without debuginfo by default to save disk space
and improve build times. In some cases, deep troubleshooting may require
debug symbols. Use the `--enable-debuginfo` argument to build a kernel with
debug symbols included.

### Publish

To "publish" the resulting build using the simple "cp" (copy) publisher run:

    $ skt.py --rc <SKTRC> --state --workdir <WORKDIR> -vv \
             publish -p cp <DIRECTORY> <URL_PREFIX>

Here `<DIRECTORY>` would be the location for the copied build artifacts, and
`URL_PREFIX` would be the string to add to prepend the filenames with
(together with a slash `/`) to construct the URLs the files will be reachable
at. The resulting URLs will be passed to other commands, such as `run`, via
the saved state in the configuration file.

E.g. to publish to the `/srv/builds` directory available at
`http://skt-server` run:

    $ skt.py --rc skt-rc --state --workdir skt-workdir -vv \
             publish -p cp /srv/builds http://skt-server

### Run

To run the tests you will need access to a
[Beaker](https://beaker-project.org/) instance configured to the point where
`bkr whoami` completes successfully. You will also need a template file for
generating a Beaker job XML, which runs the tests. The template file can
contain the following placeholder strings replaced by `skt` before submitting
the job XML to Beaker:

* `##KVER##`
    - The kernel release version output by `make -s kernelrelease`.
* `##KPKG_URL##`
    - The URL of the kernel tarball, generated and published to with
      `publish`.
* `##UID##`
    - A string of test job "tags" generated by `merge` reset by `run` on
      baseline checks. For use in `<whiteboard>`.
* `##HOSTNAME##`
    - A decorated name of the host the job runs on, or empty string if no
      specific host was picked. For use in `<whiteboard>`.
* `##HOSTNAMETAG##`
    - An XML fragment limiting the job to a specific host, or empty string if
      no specific host was picked. For use in `<hostRequires>`.

Below is an example of a superficial template. Note that it won't work as is.

```XML
<job>
  <whiteboard>skt ##KVER## ##UID## ##HOSTNAME##</whiteboard>
  <recipeSet>
    <recipe whiteboard="##KVER##">
      <distroRequires>
        <and>
          <distro_family op="=" value="Fedora26"/>
          <distro_tag op="=" value="RELEASED"/>
          <distro_variant op="=" value="Server"/>
          <distro_arch op="=" value="x86_64"/>
        </and>
      </distroRequires>
      <hostRequires>
        <and>
          <arch op="=" value="x86_64"/>
          ##HOSTNAMETAG##
        </and>
      </hostRequires>
      <repos/>
      <partitions/>
      <ks_appends/>
      <task name="/distribution/install" role="STANDALONE"/>
      <task name="/distribution/kpkginstall" role="STANDALONE">
        <params>
          <param name="KPKG_URL" value="##KPKG_URL##"/>
          <param name="KVER" value="##KVER##"/>
        </params>
      </task>
    </recipe>
  </recipeSet>
</job>
```

Provided you have both Beaker access and a suitable job XML template, you can
run the tests with the built kernel as such:

    $ skt.py --rc <SKTRC> --state --workdir <WORKDIR> -vv run \
             --runner beaker '{"jobtemplate": "<JOBTEMPLATE>"}' \
             --wait

Here, `<JOBTEMPLATE>` would be the name of the file with the Beaker job XML
template. If you remove the `--wait` option, the command will return once the
job was submitted. Otherwise it will wait for its completion and report the
result.

E.g. to run the tests from a job XML template named `beakerjob.xml`, execute:

    $ skt.py --rc <SKTRC> --state --workdir <WORKDIR> -vv run \
             --runner beaker '{"jobtemplate": "beakerjob.xml"}' \
             --wait

### Report

There are two "reporters" supported at the moment: "stdio" and "mail".
The former prints the report on stdout and the latter sends it by mail to
specified addresses, with specified "From" address.

This command *requires* the runner parameters from the "run" command to be
present in the configuration file. It needs this minimum "runner" section:

    [runner]
    type = beaker
    jobtemplate = <JOBTEMPLATE>

Here, `<JOBTEMPLATE>` is the same Beaker job template file name you used for
the "run" command. E.g., continuing from the example above, it can be:

    [runner]
    type = beaker
    jobtemplate = beakerjob.xml

After adding the above snippet to the configuration file, execute this to have
`skt` print the report on its stdout:

    $ skt.py --rc <SKTRC> --state --workdir <WORKDIR> -vv \
             report --reporter <REPORTER_TYPE> <REPORTER_PARAMS>

Here, `<REPORTER_TYPE>` would be the reporter type: either `stdio`, or `mail`,
and `<REPORTER_PARAMS>` would be the type-specific parameters in JSON
representation. The `stdio` reporter doesn't need any parameters, so you can
just pass an empty object, like this:

    $ skt.py --rc <SKTRC> --state --workdir <WORKDIR> -vv \
             report --reporter stdio '{}'

The `mail` reporter parameters are a bit more involved and to include the
"From" address, and a list of "To" addresses for the message to send:

    $ skt.py --rc <SKTRC> --state --workdir <WORKDIR> -vv
             report --reporter mail \
             '{"mailfrom": "<MAILFROM>", "mailto": "<MAILTO_LIST>"}'

Here, `<MAILFROM>` would be the "From" address, and `<MAILTO_LIST>`
would be a comma-separated list of "To" addresses.

The following example sends the report to the current user and to root on the
same host, with "From" address being the current user:

    $ skt.py --rc <SKTRC> --state --workdir <WORKDIR> -vv
             report --reporter mail
             '{"mailfrom": "'$USER'@localhost", "mailto": "'$USER'@localhost, root@localhost"}'

Note that the `report` command will reach for build artifacts via the URLs
generated by the `publish` command.

### Cleanup

The `cleanup` command doesn't have its own options, but recognizes the global
`--state` and `--wipe` options. It will remove the state section from the
configuration file, if `--state` is specified, and it will remove the working
directory, if `--wipe` is specified. Otherwise it will just remove the built
tarball and the build information file.

Developer Guide
---------------

Developers can test changes to `skt` by using "development mode" from python's
`setuptools` package. First, `cd` to the directory where `skt` is cloned and
run:

    $ pip install -e .

This installs `skt` in a mode where any changes within the repo are
immediately available simply by running `skt`. There is no need to repeatedly
run `pip install .` after each change.

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
