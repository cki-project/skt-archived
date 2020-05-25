skt - sonic kernel testing
==========================

THIS PROJECT HAS MOVED TO https://gitlab.com/cki-project/skt/.

[![Travis CI Build Status][travis_badge]][travis_page]
[![Test Coverage Status][coveralls_badge]][coveralls_page]

Skt is a tool for monitoring Beaker jobs and resubmitting them.

Dependencies
------------

Install dependencies needed for running skt like this:

    sudo dnf install -y python3 beaker-client

Extra dependencies needed for running the testsuite:

    sudo dnf install -y python3-mock

Run tests
---------

To run all tests execute:

    python3 -m unittest discover tests

To run some specific tests, you can execute a specific test like this:

    python3 -m unittest tests.test_runner


Installation
------------

Install `skt` directly from git:

    pip install git+https://github.com/RH-FMK/skt

If support for beaker is required, install ``skt`` with the ``beaker``
extras:

    pip install git+https://github.com/rh-fmk/skt.git#egg-project[beaker]

Test the `skt` executable by printing the help text:

    skt -h

Usage
-----

The `skt` tool implements several "commands", and each of those accepts its
own command-line options and arguments. However there are also several
"global" command-line options, shared by all the commands. To get a summary of
the global options and available commands, run `skt --help`. To get a
summary of particular command's options and arguments, run `skt <COMMAND>
--help`, where `<COMMAND>` would be the command of interest.

Most of command-line options can also be read by `skt` from its configuration
file, which is specified using the global `--rc` command-line option. However,
there are some command-line options which cannot be stored in the configuration
file, and there are some options read from the configuration file by some `skt`
commands, which cannot be passed via the command line. Some of the latter are
required for operation.

Most `skt` commands can write their state to the configuration file as they
work, so that the other commands can take the workflow task over from them.
Some commands can receive that state from the command line, via options, but
some require some information stored in the configuration file. For this
reason, to support a complete workflow, it is necessary to always make the
commands transfer their state via the configuration file.

To separate the actual configuration from the specific workflow's state, and
to prevent separate tasks from interfering with each other, you can store your
configuration in a separate (e.g. read-only) file, copy it to a new file each
time you want to do something, then discard the file after the task is
complete. Note that reusing a configuration file with state added can break
some commands in unexpected ways. That includes repeating a previous command
after the next command in the workflow has already ran.

The following commands are supported by `skt`:

* `run`
    - Run tests on a built kernel using the specified "runner". Only
      "Beaker" runner is currently supported. This command expects `publish`
      command to have completed succesfully.

Currently, skt is being used only to monitor Beaker test results. Section below
describes this.

All the following commands use the `-vv` option to increase verbosity of the
command's output, so it's easier to debug problems. Remove the option for
quieter, shorter output.

### Run

To run the tests you will need access to a
[Beaker](https://beaker-project.org/) instance configured to the point where
`bkr whoami` completes successfully. You will also need Beaker job XML file,
 which runs the tests. 
Below is an example of this file. Note that it won't work as is.

```XML
<job>
  <whiteboard>skt kernel-version</whiteboard>
  <recipeSet>
    <recipe whiteboard="kernel-version">
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
        </and>
      </hostRequires>
      <repos/>
      <partitions/>
      <ks_appends/>
      <task name="/distribution/install" role="STANDALONE"/>
      <task name="/distribution/kpkginstall" role="STANDALONE">
        <params>
          <param name="KPKG_URL" value="http://url_to_kernel"/>
          <param name="KVER" value="kernel-version"/>
        </params>
      </task>
    </recipe>
  </recipeSet>
</job>
```

Provided you have both Beaker access and a suitable job XML file, you can
run the tests with the built kernel as such:

    skt --rc <SKTRC> --state --workdir <WORKDIR> -vv run --wait

The `<SKTRC>` is a config file with contents like this:

[runner]
jobtemplate=beaker.xml
jobowner=username
blacklist=beaker-blacklist.txt

Here, `<jobtemplate>` is the name of the file with the Beaker job XML
file. If you remove the `--wait` option, the command will return once the
job was submitted. Otherwise it will wait for its completion and report the
result.

In case running on specific hosts is not desired, one can use a simple text
file containing one hostname per line, and pass the file via `blacklist`
parameter. Tests will not attempt to run on machines which names are specified
in the file. This is useful for example as a temporary fix in case the hardware
is buggy and the maintainer of the pool doesn't have time to exclude it from
the pool.

Developer Guide
---------------

Developers can test changes to `skt` by using "development mode" from python's
`setuptools` package. First, `cd` to the directory where `skt` is cloned and
run:

    pip install --user -e .

This installs `skt` in a mode where any changes within the repo are
immediately available simply by running `skt`. There is no need to repeatedly
run `pip install .` after each change.

Using a virtual environment is highly recommended. This keeps `skt` and all
its dependencies in a separate Python environment. Developers can build a
virtual environment for skt quickly:

    virtualenv ~/skt-venv/
    source ~/skt-venv/bin/activate
    pip install -e .

To deactivate the virtual environment, simply run `deactivate`.

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

[travis_badge]: https://travis-ci.org/RH-FMK/skt.svg?branch=master
[travis_page]: https://travis-ci.org/RH-FMK/skt
[coveralls_badge]: https://coveralls.io/repos/github/RH-FMK/skt/badge.svg?branch=master
[coveralls_page]: https://coveralls.io/github/RH-FMK/skt?branch=master
