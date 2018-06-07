#!/usr/bin/python2

# Copyright (c) 2017 Red Hat, Inc. All rights reserved. This copyrighted
# material is made available to anyone wishing to use, modify, copy, or
# redistribute it subject to the terms and conditions of the GNU General
# Public License v.2 or later.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

import ConfigParser
import argparse
import datetime
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import traceback

import junit_xml

import skt
import skt.publisher
import skt.reporter
import skt.runner
from skt.kernelbuilder import KernelBuilder
from skt.kerneltree import KernelTree
import skt.state_file as state_file

DEFAULTRC = "~/.sktrc"
DEFAULT_STATE_FILE = 'skt-state.yml'
LOGGER = logging.getLogger()
retcode = 0


def full_path(path):
    """Get an absolute path to a file"""
    return os.path.abspath(os.path.expanduser(path))


def junit(func):
    """
    Create a function accepting a configuration object and passing it to
    the specified function, putting the call results into a JUnit test case,
    if configuration has JUnit result directory specified, simply calling the
    function otherwise.

    The generated test case is named "skt.<function-name>". The case stdout is
    set to JSON representation of the configuration object after the function
    call has completed. The created test case is appended to the
    "_testcases" list in the configuration object after that. Sets the global
    "retcode" to 1 in case the function throws an exception. The testcase is
    considered failed if the function throws an exeption or if the global
    "retcode" is set to anything but zero after function returns.

    Args:
        func:   The function to call in the created function. Must accept
                a configuration object as the argument. Return value would be
                ignored. Can set the global "retcode" to indicate success
                (zero) or failure (non-zero).

    Return:
        The created function.
    """
    def wrapper(state):
        global retcode
        if state.get('junit'):
            tstart = time.time()
            tc = junit_xml.TestCase(func.__name__, classname="skt")

            try:
                func(state)
            except Exception:
                logging.error("Exception caught: %s", traceback.format_exc())
                tc.add_failure_info(traceback.format_exc())
                retcode = 1

            # No exception but retcode != 0, probably tests failed
            if retcode != 0 and not tc.is_failure():
                tc.add_failure_info("Step finished with retcode: %d" % retcode)

            tc.stdout = json.dumps(state, default=str)
            tc.elapsed_sec = time.time() - tstart
            state['_testcases'].append(tc)
        else:
            func(state)
    return wrapper


@junit
def cmd_merge(state):
    """
    Fetch a kernel repository, checkout particular references, and optionally
    apply patches from patchwork instances.

    Args:
        state:    A dictionary of skt state.
    """
    global retcode
    utypes = []
    ktree = KernelTree(
        state.get('baserepo'),
        ref=state.get('ref'),
        wdir=state.get('workdir'),
        fetch_depth=state.get('fetch_depth')
    )
    bhead = ktree.checkout()
    commitdate = ktree.get_commit_date(bhead)

    # Save state data about the merge before applying any patches
    state_update = {
        'baserepo': state.get('baserepo'),
        'basehead': bhead,
        'commitdate': commitdate
    }
    state = state_file.update(state, state_update)

    try:
        idx = 0
        for mb in state.get('merge_ref'):

            # Save state data about the ref we are merging
            state_update = {
                'mergerepo_%02d' % idx: mb[0],
                'mergehead_%02d' % idx: bhead
            }
            state = state_file.update(state, state_update)

            utypes.append("[git]")
            idx += 1
            if retcode:
                return

        if state.get('patchlist'):
            utypes.append("[local patch]")
            idx = 0
            for patch in state.get('patchlist'):

                # Save state data about this local patch
                state = state_file.update(state,
                                          {'localpatch_%02d' % idx: patch})

                ktree.merge_patch_file(os.path.abspath(patch))
                idx += 1

        if state.get('pw'):
            utypes.append("[patchwork]")
            idx = 0
            for patch in state.get('pw'):

                # Save state data about this patchwork patch
                state = state_file.update(state,
                                          {'patchwork_%02d' % idx: patch})

                ktree.merge_patchwork_patch(patch)
                idx += 1
    except Exception as e:
        # Save state data about any exceptions that occurred during the merge
        state = state_file.update(state, {'mergelog': ktree.mergelog})
        raise e

    uid = "[baseline]"
    if utypes:
        uid = " ".join(utypes)

    kpath = ktree.getpath()
    buildinfo = ktree.dumpinfo()
    buildhead = ktree.get_commit_hash()

    # Now that the merging is complete, save state data about the results of
    # the merge.
    state_update = {
        'workdir': kpath,
        'buildinfo': buildinfo,
        'buildhead': buildhead,
        'uid': uid
    }
    state = state_file.update(state, state_update)


@junit
def cmd_build(state):
    """
    Build the kernel with specified configuration and put it into a tarball.

    Args:
        state:    A dictionary of skt state.
    """
    tstamp = datetime.datetime.strftime(datetime.datetime.now(),
                                        "%Y%m%d%H%M%S")

    builder = KernelBuilder(
        source_dir=state.get('workdir'),
        basecfg=state.get('baseconfig'),
        cfgtype=state.get('cfgtype'),
        extra_make_args=state.get('makeopts'),
        enable_debuginfo=state.get('enable_debuginfo')
    )

    # Clean the kernel source with 'make mrproper' if requested.
    if state.get('wipe'):
        builder.clean_kernel_source()

    try:
        tgz = builder.mktgz()
    except Exception as e:
        # Save state data about any exceptions during the kernel build
        state = state_file.update(state, {'buildlog': builder.buildlog})
        raise e

    if state.get('buildhead'):
        ttgz = "%s.tar.gz" % state.get('buildhead')
    else:
        ttgz = addtstamp(tgz, tstamp)
    os.rename(tgz, ttgz)
    logging.info("tarball path: %s", ttgz)

    tbuildinfo = None
    if state.get('buildinfo'):
        if state.get('buildhead'):
            tbuildinfo = "%s.csv" % state.get('buildhead')
        else:
            tbuildinfo = addtstamp(state.get('buildinfo'), tstamp)
        os.rename(state.get('buildinfo'), tbuildinfo)

    tconfig = "%s.config" % tbuildinfo
    shutil.copyfile(builder.get_cfgpath(), tconfig)

    krelease = builder.getrelease()

    # Save state data about the completed build
    state_update = {
        'tarpkg': ttgz,
        'buildinfo': tbuildinfo,
        'buildconf': tconfig,
        'krelease': krelease
    }
    state = state_file.update(state, state_update)


@junit
def cmd_publish(state):
    """
    Publish (copy) the kernel tarball, configuration, and build information to
    the specified location, generating their resulting URLs, using the
    specified "publisher". Only "cp" and "scp" pusblishers are supported at the
    moment.

    Args:
        state:    A dictionary of skt state.
    """
    publisher = skt.publisher.getpublisher(*state.get('publisher'))

    if not state.get('tarpkg'):
        raise Exception("skt publish is missing \"--tarpkg <path>\" option")

    infourl = None
    cfgurl = None

    url = publisher.publish(state.get('tarpkg'))
    logging.info("published url: %s", url)

    if state.get('buildinfo'):
        infourl = publisher.publish(state.get('buildinfo'))

    if state.get('buildconf'):
        cfgurl = publisher.publish(state.get('buildconf'))

    # Save state data for the publishing of the kernel build
    state_update = {
        'buildurl': url,
        'cfgurl': cfgurl,
        'infourl': infourl
    }
    state = state_file.update(state, state_update)


@junit
def cmd_run(state):
    """
    Run tests on a built kernel using the specified "runner". Only "Beaker"
    runner is currently supported.

    Args:
        state:    A dictionary of skt state.
    """
    global retcode
    runner = skt.runner.getrunner(*state.get('runner'))
    retcode = runner.run(state.get('buildurl'), state.get('krelease'),
                         state.get('wait'), uid=state.get('uid'))

    idx = 0
    for job in runner.jobs:
        if state.get('wait') and state.get('junit'):
            runner.dumpjunitresults(job, state.get('junit'))

        # Save the Beaker jobid to the state file
        state = state_file.update(state, {'jobid_%s' % (idx): job})

        idx += 1

    state['jobs'] = runner.jobs

    if retcode and state.get('basehead') and state.get('publisher') \
            and state.get('basehead') != state.get('buildhead'):
        # TODO: there is a chance that baseline 'krelease' is different
        baserunner = skt.runner.getrunner(*state.get('runner'))
        publisher = skt.publisher.getpublisher(*state.get('publisher'))
        baseurl = publisher.geturl("%s.tar.gz" % state.get('basehead'))
        basehost = runner.get_mfhost()
        baseres = baserunner.run(baseurl, state.get('krelease'),
                                 state.get('wait'),
                                 host=basehost, uid="baseline check",
                                 reschedule=False)

        # Save the result of the beaker job call to the state file
        state = state_file.update(state, {'baseretcode': baseres})

        # If baseline also fails - assume pass
        if baseres:
            retcode = 0

    # Save the return code of the call to beaker to start a job
    state = state_file.update(state, {'retcode': retcode})


def cmd_report(state):
    """
    Report build and/or test results using the specified "reporter". Currently
    results can be reported by e-mail or printed to stdout.

    Args:
        state:    A dictionary of skt state.
    """
    if not state.get("reporter"):
        return

    # FIXME This is violation of composition. This basically passes the whole
    # configuration object to reporter, so it can access anything. Pass the
    # needed data explicitly instead, or deal with it outside reporter, if
    # that is unsuitable.
    state['reporter'][1].update({'state': state})
    reporter = skt.reporter.getreporter(*state.get('reporter'))
    reporter.report()


def cmd_cleanup(state):
    """
    Remove the build information file, kernel tarball. Remove state information
    from the configuration file, if saving state was enabled with the global
    --state option, and remove the whole working directory, if the global
    --wipe option was specified.

    Args:
        state:    A dictionary of skt state.
    """
    if state.get('buildinfo'):
        try:
            os.unlink(state.get('buildinfo'))
        except OSError:
            pass

    if state.get('tarpkg'):
        try:
            os.unlink(state.get('tarpkg'))
        except OSError:
            pass

    if state.get('wipe') and state.get('workdir'):
        shutil.rmtree(state.get('workdir'))


def cmd_all(state):
    """
    Run the following commands in order: merge, build, publish, run, report (if
    --wait option was specified), and cleanup.

    Args:
        state:    A dictionary of skt state.
    """
    cmd_merge(state)
    cmd_build(state)
    cmd_publish(state)
    cmd_run(state)
    if state.get('wait'):
        cmd_report(state)
    cmd_cleanup(state)


def addtstamp(path, tstamp):
    """
    Add time stamp to a file path.

    Args:
        path:   file path.
        tstamp: time stamp.

    Returns:
        New path with time stamp.
    """
    return os.path.join(os.path.dirname(path),
                        "%s-%s" % (tstamp, os.path.basename(path)))


def setup_logging(verbose):
    """
    Setup the root logger.

    Args:
        verbose:    Verbosity level to setup log message filtering.
    """
    logging.basicConfig(format="%(asctime)s %(levelname)8s   %(message)s")
    LOGGER.setLevel(logging.WARNING - (verbose * 10))


def setup_parser():
    """
    Create an skt command line parser.

    Returns:
        The created parser.
    """
    parser = argparse.ArgumentParser()

    # These arguments apply to all commands within skt
    parser.add_argument(
        "-d",
        "--workdir",
        type=str,
        help="Path to work dir"
    )
    parser.add_argument(
        "-w",
        "--wipe",
        help=(
            "Clean build (make mrproper before building) and remove workdir"
            "when finished"
        ),
        action="store_true",
        default=False
    )
    parser.add_argument(
        "--junit",
        help="Directory for storing junit XML results"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="Increase verbosity level",
        action="count",
        default=0
    )
    parser.add_argument(
        "--rc",
        help="Path to rc file",
        default=DEFAULTRC
    )
    parser.add_argument(
        "--state",
        help=(
            "Path to the state file (holds information throughout testing)"
        ),
        default=DEFAULT_STATE_FILE
    )

    subparsers = parser.add_subparsers()

    # These arguments apply to the 'merge' skt subcommand
    parser_merge = subparsers.add_parser("merge", add_help=False)
    parser_merge.add_argument(
        "-b",
        "--baserepo",
        type=str,
        help="Base repo URL"
    )
    parser_merge.add_argument(
        "--ref",
        type=str,
        help="Base repo ref to which patches are applied (default: master)"
    )
    parser_merge.add_argument(
        "--patchlist",
        type=str,
        nargs="+",
        help="Paths to each local patch to apply (space delimited)"
    )
    parser_merge.add_argument(
        "--pw",
        type=str,
        nargs="+",
        help="URLs to each Patchwork patch to apply (space delimited)"
    )
    parser_merge.add_argument(
        "-m",
        "--merge-ref",
        nargs="+",
        help="Merge ref format: 'url [ref]'",
        action="append",
        default=[]
    )
    parser_merge.add_argument(
        "--fetch-depth",
        type=str,
        help=(
            "Create a shallow clone with a history truncated to the "
            "specified number of commits."
        ),
        default=None
    )

    # These arguments apply to the 'build' skt command
    parser_build = subparsers.add_parser("build", add_help=False)
    parser_build.add_argument(
        "-c",
        "--baseconfig",
        type=str,
        help="Path to kernel config to use"
    )
    parser_build.add_argument(
        "--cfgtype",
        type=str,
        help="How to process default config (default: olddefconfig)"
    )
    parser_build.add_argument(
        "--enable-debuginfo",
        type=bool,
        default=False,
        help="Build kernel with debuginfo (default: disabled)"
    )
    parser_build.add_argument(
        "--makeopts",
        type=str,
        help="Additional options to pass to make"
    )

    # These arguments apply to the 'publish' skt command
    parser_publish = subparsers.add_parser("publish", add_help=False)
    parser_publish.add_argument(
        "-p",
        "--publisher",
        type=str,
        nargs=3,
        help="Publisher config string in 'type destination baseurl' format"
    )
    parser_publish.add_argument(
        "--tarpkg",
        type=str,
        help="Path to tar pkg to publish"
    )
    parser_publish.add_argument(
        "--buildinfo",
        type=str,
        help="Path to accompanying buildinfo"
    )

    # These arguments apply to the 'run' skt command
    parser_run = subparsers.add_parser("run", add_help=False)
    parser_run.add_argument(
        "-r",
        "--runner",
        nargs=2,
        type=str,
        help="Runner config in 'type \"{'key' : 'val', ...}\"' format"
    )
    parser_run.add_argument(
        "--buildurl",
        type=str,
        help="Build tarpkg url"
    )
    parser_run.add_argument(
        "--krelease",
        type=str,
        help="Kernel release version of the build"
    )
    parser_run.add_argument(
        "--wait",
        action="store_true",
        default=False,
        help="Do not exit until tests are finished"
    )

    # These arguments apply to the 'report' skt subcommand
    parser_report = subparsers.add_parser("report", add_help=False)
    parser_report.add_argument(
        "--reporter",
        nargs=2,
        type=str,
        help="Reporter config in 'type \"{'key' : 'val', ...}\"' format")
    parser_report.set_defaults(func=cmd_report)
    parser_report.set_defaults(_name="report")

    parser_cleanup = subparsers.add_parser("cleanup", add_help=False)

    parser_all = subparsers.add_parser(
        "all",
        parents=[
            parser_merge,
            parser_build,
            parser_publish,
            parser_run,
            parser_report,
            parser_cleanup
        ]
    )

    parser_merge.add_argument(
        "-h",
        "--help",
        help="Merge sub-command help",
        action="help"
    )
    parser_build.add_argument(
        "-h",
        "--help",
        help="Build sub-command help",
        action="help"
    )
    parser_publish.add_argument(
        "-h",
        "--help",
        action="help",
        help="Publish sub-command help"
    )
    parser_run.add_argument(
        "-h",
        "--help",
        help="Run sub-command help",
        action="help"
    )
    parser_report.add_argument(
        "-h",
        "--help",
        help="Report sub-command help",
        action="help"
    )

    parser_merge.set_defaults(func=cmd_merge)
    parser_merge.set_defaults(_name="merge")
    parser_build.set_defaults(func=cmd_build)
    parser_build.set_defaults(_name="build")
    parser_publish.set_defaults(func=cmd_publish)
    parser_publish.set_defaults(_name="publish")
    parser_run.set_defaults(func=cmd_run)
    parser_run.set_defaults(_name="run")
    parser_cleanup.set_defaults(func=cmd_cleanup)
    parser_cleanup.set_defaults(_name="cleanup")
    parser_all.set_defaults(func=cmd_all)
    parser_all.set_defaults(_name="all")

    return parser


def load_config(args):
    """
    Load skt configuration from the command line and the configuration file.

    Args:
        args:   Parsed command-line configuration, including the path to the
                configuration file.

    Returns:
        Loaded configuration dictionary.
    """
    cfg = {}

    # NOTE(mhayden): The shell should do any tilde expansions on the path
    # before the rc path is provided to Python.
    config = ConfigParser.ConfigParser()
    config.read(os.path.expanduser(args.rc))

    if config.has_section('config'):
        for (name, value) in config.items('config'):
            if not cfg.get(name):
                cfg[name] = value

    if config.has_section('publisher'):
        cfg['publisher'] = [config.get('publisher', 'type'),
                            config.get('publisher', 'destination'),
                            config.get('publisher', 'baseurl')]

    if config.has_section('runner'):
        rcfg = {}
        for (key, val) in config.items('runner'):
            if key != 'type':
                rcfg[key] = val
        cfg['runner'] = [config.get('runner', 'type'), rcfg]

    if config.has_section('reporter'):
        rcfg = {}
        for (key, val) in config.items('reporter'):
            if key != 'type':
                rcfg[key] = val
        cfg['reporter'] = [config.get('reporter', 'type'), rcfg]

    # Get an absolute path for the work directory
    if cfg.get('workdir'):
        cfg['workdir'] = full_path(cfg.get('workdir'))
    else:
        cfg['workdir'] = tempfile.mkdtemp()

    return cfg


def main():
    global retcode

    parser = setup_parser()
    args = parser.parse_args()

    setup_logging(args.verbose)

    state = state_file.read(args.state)

    # If state file does not exist, create it using config file as defaults
    if not os.path.isfile(args.state):
        # Load skt's config from the rc file
        cfg = load_config(args)
        state = state_file.update(state, cfg)

    # let command line args overwrite options in saved state file
    state = state_file.update(state, vars(args))

    args.func(state)
    if state.get('junit'):
        ts = junit_xml.TestSuite("skt", state.get('_testcases'))
        junit = state.get('junit')
        with open("%s/%s.xml" % (junit, args._name), 'w') as fileh:
            junit_xml.TestSuite.to_file(fileh, [ts])

    sys.exit(retcode)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        # cleanup??
        print("\nExited at user request.")
        sys.exit(1)
