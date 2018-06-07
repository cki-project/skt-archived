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
import ast
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

DEFAULTRC = "~/.sktrc"
LOGGER = logging.getLogger()
retcode = 0


def full_path(path):
    """Get an absolute path to a file"""
    return os.path.abspath(os.path.expanduser(path))


def save_state(cfg, state):
    """
    Merge state to cfg, and then save cfg.

    Args:
        cfg:    A dictionary of skt configuration.
        state:  A dictionary of skt current state.
    """

    for (key, val) in state.iteritems():
        cfg[key] = val

    if not cfg.get('state'):
        return

    config = cfg.get('_parser')
    if not config.has_section("state"):
        config.add_section("state")

    for (key, val) in state.iteritems():
        if val is not None:
            logging.debug("state: %s -> %s", key, val)
            config.set('state', key, val)

    with open(cfg.get('rc'), 'w') as fileh:
        config.write(fileh)


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
    def wrapper(cfg):
        global retcode
        if cfg.get('junit'):
            tstart = time.time()
            tc = junit_xml.TestCase(func.__name__, classname="skt")

            try:
                func(cfg)
            except Exception:
                logging.error("Exception caught: %s", traceback.format_exc())
                tc.add_failure_info(traceback.format_exc())
                retcode = 1

            # No exception but retcode != 0, probably tests failed
            if retcode != 0 and not tc.is_failure():
                tc.add_failure_info("Step finished with retcode: %d" % retcode)

            tc.stdout = json.dumps(cfg, default=str)
            tc.elapsed_sec = time.time() - tstart
            cfg['_testcases'].append(tc)
        else:
            func(cfg)
    return wrapper


@junit
def cmd_merge(cfg):
    """
    Fetch a kernel repository, checkout particular references, and optionally
    apply patches from patchwork instances.

    Args:
        cfg:    A dictionary of skt configuration.
    """
    global retcode
    utypes = []
    ktree = KernelTree(
        cfg.get('baserepo'),
        ref=cfg.get('ref'),
        wdir=cfg.get('workdir'),
        fetch_depth=cfg.get('fetch_depth')
    )
    bhead = ktree.checkout()
    commitdate = ktree.get_commit_date(bhead)
    save_state(cfg, {'baserepo': cfg.get('baserepo'),
                     'basehead': bhead,
                     'commitdate': commitdate})

    try:
        idx = 0
        for mb in cfg.get('merge_ref'):
            save_state(cfg, {'mergerepo_%02d' % idx: mb[0],
                             'mergehead_%02d' % idx: bhead})
            (retcode, _) = ktree.merge_git_ref(*mb)

            utypes.append("[git]")
            idx += 1
            if retcode:
                return

        if cfg.get('patchlist'):
            utypes.append("[local patch]")
            idx = 0
            for patch in cfg.get('patchlist'):
                save_state(cfg, {'localpatch_%02d' % idx: patch})
                ktree.merge_patch_file(os.path.abspath(patch))
                idx += 1

        if cfg.get('pw'):
            utypes.append("[patchwork]")
            idx = 0
            for patch in cfg.get('pw'):
                save_state(cfg, {'patchwork_%02d' % idx: patch})
                ktree.merge_patchwork_patch(patch)
                idx += 1
    except Exception as e:
        save_state(cfg, {'mergelog': ktree.mergelog})
        raise e

    uid = "[baseline]"
    if utypes:
        uid = " ".join(utypes)

    kpath = ktree.getpath()
    buildinfo = ktree.dumpinfo()
    buildhead = ktree.get_commit_hash()

    save_state(cfg, {'workdir': kpath,
                     'buildinfo': buildinfo,
                     'buildhead': buildhead,
                     'uid': uid})


@junit
def cmd_build(cfg):
    """
    Build the kernel with specified configuration and put it into a tarball.

    Args:
        cfg:    A dictionary of skt configuration.
    """
    tstamp = datetime.datetime.strftime(datetime.datetime.now(),
                                        "%Y%m%d%H%M%S")

    builder = KernelBuilder(
        source_dir=cfg.get('workdir'),
        basecfg=cfg.get('baseconfig'),
        cfgtype=cfg.get('cfgtype'),
        extra_make_args=cfg.get('makeopts'),
        enable_debuginfo=cfg.get('enable_debuginfo')
    )

    # Clean the kernel source with 'make mrproper' if requested.
    if cfg.get('wipe'):
        builder.clean_kernel_source()

    try:
        tgz = builder.mktgz()
    except Exception as e:
        save_state(cfg, {'buildlog': builder.buildlog})
        raise e

    if cfg.get('buildhead'):
        ttgz = "%s.tar.gz" % cfg.get('buildhead')
    else:
        ttgz = addtstamp(tgz, tstamp)
    os.rename(tgz, ttgz)
    logging.info("tarball path: %s", ttgz)

    tbuildinfo = None
    if cfg.get('buildinfo'):
        if cfg.get('buildhead'):
            tbuildinfo = "%s.csv" % cfg.get('buildhead')
        else:
            tbuildinfo = addtstamp(cfg.get('buildinfo'), tstamp)
        os.rename(cfg.get('buildinfo'), tbuildinfo)

    tconfig = "%s.config" % tbuildinfo
    shutil.copyfile(builder.get_cfgpath(), tconfig)

    krelease = builder.getrelease()

    save_state(cfg, {'tarpkg': ttgz,
                     'buildinfo': tbuildinfo,
                     'buildconf': tconfig,
                     'krelease': krelease})


@junit
def cmd_publish(cfg):
    """
    Publish (copy) the kernel tarball, configuration, and build information to
    the specified location, generating their resulting URLs, using the
    specified "publisher". Only "cp" and "scp" pusblishers are supported at the
    moment.

    Args:
        cfg:    A dictionary of skt configuration.
    """
    publisher = skt.publisher.getpublisher(*cfg.get('publisher'))

    if not cfg.get('tarpkg'):
        raise Exception("skt publish is missing \"--tarpkg <path>\" option")

    infourl = None
    cfgurl = None

    url = publisher.publish(cfg.get('tarpkg'))
    logging.info("published url: %s", url)

    if cfg.get('buildinfo'):
        infourl = publisher.publish(cfg.get('buildinfo'))

    if cfg.get('buildconf'):
        cfgurl = publisher.publish(cfg.get('buildconf'))

    save_state(cfg, {'buildurl': url,
                     'cfgurl': cfgurl,
                     'infourl': infourl})


@junit
def cmd_run(cfg):
    """
    Run tests on a built kernel using the specified "runner". Only "Beaker"
    runner is currently supported.

    Args:
        cfg:    A dictionary of skt configuration.
    """
    global retcode
    runner = skt.runner.getrunner(*cfg.get('runner'))
    retcode = runner.run(cfg.get('buildurl'), cfg.get('krelease'),
                         cfg.get('wait'), uid=cfg.get('uid'))

    idx = 0
    for job in runner.jobs:
        if cfg.get('wait') and cfg.get('junit'):
            runner.dumpjunitresults(job, cfg.get('junit'))
        save_state(cfg, {'jobid_%s' % (idx): job})
        idx += 1

    cfg['jobs'] = runner.jobs

    if retcode and cfg.get('basehead') and cfg.get('publisher') \
            and cfg.get('basehead') != cfg.get('buildhead'):
        # TODO: there is a chance that baseline 'krelease' is different
        baserunner = skt.runner.getrunner(*cfg.get('runner'))
        publisher = skt.publisher.getpublisher(*cfg.get('publisher'))
        baseurl = publisher.geturl("%s.tar.gz" % cfg.get('basehead'))
        basehost = runner.get_mfhost()
        baseres = baserunner.run(baseurl, cfg.get('krelease'), cfg.get('wait'),
                                 host=basehost, uid="baseline check",
                                 reschedule=False)
        save_state(cfg, {'baseretcode': baseres})

        # If baseline also fails - assume pass
        if baseres:
            retcode = 0

    save_state(cfg, {'retcode': retcode})


def cmd_report(cfg):
    """
    Report build and/or test results using the specified "reporter". Currently
    results can be reported by e-mail or printed to stdout.

    Args:
        cfg:    A dictionary of skt configuration.
    """
    if not cfg.get("reporter"):
        return

    # FIXME This is violation of composition. This basically passes the whole
    # configuration object to reporter, so it can access anything. Pass the
    # needed data explicitly instead, or deal with it outside reporter, if
    # that is unsuitable.
    cfg['reporter'][1].update({'cfg': cfg})
    reporter = skt.reporter.getreporter(*cfg.get('reporter'))
    reporter.report()


def cmd_cleanup(cfg):
    """
    Remove the build information file, kernel tarball. Remove state information
    from the configuration file, if saving state was enabled with the global
    --state option, and remove the whole working directory, if the global
    --wipe option was specified.

    Args:
        cfg:    A dictionary of skt configuration.
    """
    config = cfg.get('_parser')
    if config.has_section('state'):
        config.remove_section('state')
        with open(cfg.get('rc'), 'w') as fileh:
            config.write(fileh)

    if cfg.get('buildinfo'):
        try:
            os.unlink(cfg.get('buildinfo'))
        except OSError:
            pass

    if cfg.get('tarpkg'):
        try:
            os.unlink(cfg.get('tarpkg'))
        except OSError:
            pass

    if cfg.get('wipe') and cfg.get('workdir'):
        shutil.rmtree(cfg.get('workdir'))


def cmd_all(cfg):
    """
    Run the following commands in order: merge, build, publish, run, report (if
    --wait option was specified), and cleanup.

    Args:
        cfg:    A dictionary of skt configuration.
    """
    cmd_merge(cfg)
    cmd_build(cfg)
    cmd_publish(cfg)
    cmd_run(cfg)
    if cfg.get('wait'):
        cmd_report(cfg)
    cmd_cleanup(cfg)


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
    # FIXME Storing state in config file can break the whole system in case
    #       state saving aborts. It's better to save state separately.
    #       It also breaks separation of concerns, as in principle skt doesn't
    #       need to modify its own configuration otherwise.
    parser.add_argument(
        "--state",
        help=(
            "Save/read state from 'state' section of rc file"
        ),
        action="store_true",
        default=False
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
    # NOTE(mhayden): The shell should do any tilde expansions on the path
    # before the rc path is provided to Python.
    config = ConfigParser.ConfigParser()
    config.read(os.path.abspath(args.rc))

    cfg = vars(args)
    cfg['_parser'] = config
    cfg['_testcases'] = []

    # Read 'state' section first so that it is not overwritten by 'config'
    # section values.
    if cfg.get('state') and config.has_section('state'):
        for (name, value) in config.items('state'):
            if not cfg.get(name):
                if name.startswith("jobid_"):
                    if "jobs" not in cfg:
                        cfg["jobs"] = set()
                    cfg["jobs"].add(value)
                elif name.startswith("mergerepo_"):
                    if "mergerepos" not in cfg:
                        cfg["mergerepos"] = list()
                    cfg["mergerepos"].append(value)
                elif name.startswith("mergehead_"):
                    if "mergeheads" not in cfg:
                        cfg["mergeheads"] = list()
                    cfg["mergeheads"].append(value)
                elif name.startswith("localpatch_"):
                    if "localpatches" not in cfg:
                        cfg["localpatches"] = list()
                    cfg["localpatches"].append(value)
                elif name.startswith("patchwork_"):
                    if "patchworks" not in cfg:
                        cfg["patchworks"] = list()
                    cfg["patchworks"].append(value)
                cfg[name] = value

    if config.has_section('config'):
        for (name, value) in config.items('config'):
            if not cfg.get(name):
                cfg[name] = value

    if config.has_section('publisher') and not cfg.get('publisher'):
        cfg['publisher'] = [config.get('publisher', 'type'),
                            config.get('publisher', 'destination'),
                            config.get('publisher', 'baseurl')]

    if config.has_section('runner') and not cfg.get('runner'):
        rcfg = {}
        for (key, val) in config.items('runner'):
            if key != 'type':
                rcfg[key] = val
        cfg['runner'] = [config.get('runner', 'type'), rcfg]
    elif cfg.get('runner'):
        cfg['runner'] = [cfg.get('runner')[0],
                         ast.literal_eval(cfg.get('runner')[1])]

    if config.has_section('reporter') and not cfg.get('reporter'):
        rcfg = {}
        for (key, val) in config.items('reporter'):
            if key != 'type':
                rcfg[key] = val
        cfg['reporter'] = [config.get('reporter', 'type'), rcfg]
    elif cfg.get('reporter'):
        cfg['reporter'] = [cfg.get('reporter')[0],
                           ast.literal_eval(cfg.get('reporter')[1])]

    for section in config.sections():
        if section.startswith("merge-"):
            mdesc = [config.get(section, 'url')]
            if config.has_option(section, 'ref'):
                mdesc.append(config.get(section, 'ref'))
            cfg['merge_ref'].append(mdesc)

    # Get an absolute path for the work directory
    if cfg.get('workdir'):
        cfg['workdir'] = full_path(cfg.get('workdir'))
    else:
        cfg['workdir'] = tempfile.mkdtemp()

    # Get an absolute path for the kernel configuration file
    if cfg.get('basecfg'):
        cfg['basecfg'] = full_path(cfg.get('basecfg'))

    # Get an absolute path for the configuration file
    if cfg.get('rc'):
        cfg['rc'] = full_path(cfg.get('rc'))

    # Get an absolute path for the buildinfo
    if cfg.get('buildinfo'):
        cfg['buildinfo'] = full_path(cfg.get('buildinfo'))

    # Get an absolute path for the buildconf
    if cfg.get('buildconf'):
        cfg['buildconf'] = full_path(cfg.get('buildconf'))

    # Get an absolute path for the tarpkg
    if cfg.get('tarpkg'):
        cfg['tarpkg'] = full_path(cfg.get('tarpkg'))

    return cfg


def main():
    global retcode

    parser = setup_parser()
    args = parser.parse_args()

    setup_logging(args.verbose)
    cfg = load_config(args)

    args.func(cfg)
    if cfg.get('junit'):
        ts = junit_xml.TestSuite("skt", cfg.get('_testcases'))
        with open("%s/%s.xml" % (cfg.get('junit'), args._name), 'w') as fileh:
            junit_xml.TestSuite.to_file(fileh, [ts])

    sys.exit(retcode)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        # cleanup??
        print("\nExited at user request.")
        sys.exit(1)
