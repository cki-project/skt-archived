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
import junit_xml
import logging
import os
import shutil
import sys
import time
import skt.publisher
import skt.reporter
import skt.runner
import skt

DEFAULTRC = "~/.sktrc"
logger = logging.getLogger()
retcode = 0


def save_state(cfg, state):
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

    # FIXME Move expansion up the call stack, as this limits the function
    # usefulness, because tilde is a valid path character.
    with open(os.path.expanduser(cfg.get('rc')), 'w') as fp:
        config.write(fp)


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
        if cfg.get('junit') is not None:
            tstart = time.time()
            tc = junit_xml.TestCase(func.__name__, classname="skt")

            try:
                func(cfg)
            except Exception as e:
                logging.error("Exception caught: %s", e)
                tc.add_failure_info(str(e))
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
    global retcode
    utypes = []
    ktree = skt.ktree(cfg.get('baserepo'),
                      ref=cfg.get('ref'),
                      wdir=cfg.get('workdir'))
    bhead = ktree.checkout()
    commitdate = ktree.get_commit_date(bhead)
    save_state(cfg, {'baserepo': cfg.get('baserepo'),
                     'basehead': bhead,
                     'commitdate': commitdate})

    try:
        idx = 0
        for mb in cfg.get('merge_ref'):
            save_state(cfg, {'meregerepo_%02d' % idx: mb[0],
                             'mergehead_%02d' % idx: head})
            (retcode, head) = ktree.merge_git_ref(*mb)

            utypes.append("[git]")
            idx += 1
            if retcode != 0:
                return

        if cfg.get('patchlist') is not None:
            utypes.append("[local patch]")
            idx = 0
            for patch in cfg.get('patchlist'):
                save_state(cfg, {'localpatch_%02d' % idx: patch})
                ktree.merge_patch_file(patch)
                idx += 1

        if cfg.get('pw') is not None:
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
    if len(utypes):
        uid = " ".join(utypes)

    kpath = ktree.getpath()
    buildinfo = ktree.dumpinfo()
    buildhead = ktree.get_commit()

    save_state(cfg, {'workdir': kpath,
                     'builddir': cfg.get('builddir'),
                     'buildinfo': buildinfo,
                     'buildhead': buildhead,
                     'uid': uid})


@junit
def cmd_build(cfg):
    tstamp = datetime.datetime.strftime(datetime.datetime.now(),
                                        "%Y%m%d%H%M%S")

    builder = skt.kbuilder(cfg.get('workdir'), cfg.get('builddir'),
                           cfg.get('baseconfig'), cfg.get('cfgtype'),
                           cfg.get('makeopts'))

    try:
        tgz = builder.mktgz(cfg.get('wipe'))
    except Exception as e:
        save_state(cfg, {'buildlog': builder.buildlog})
        raise e

    if cfg.get('buildhead') is not None:
        ttgz = "%s.tar.gz" % cfg.get('buildhead')
    else:
        ttgz = addtstamp(tgz, tstamp)
    os.rename(tgz, ttgz)
    logging.info("tarball path: %s", ttgz)

    tbuildinfo = None
    if cfg.get('buildinfo') is not None:
        if cfg.get('buildhead') is not None:
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
    publisher = skt.publisher.getpublisher(*cfg.get('publisher'))

    infourl = None
    url = publisher.publish(cfg.get('tarpkg'))
    logging.info("published url: %s", url)

    if cfg.get('buildinfo') is not None:
        infourl = publisher.publish(cfg.get('buildinfo'))

    if cfg.get('buildconf') is not None:
        cfgurl = publisher.publish(cfg.get('buildconf'))

    save_state(cfg, {'buildurl': url,
                     'cfgurl': cfgurl,
                     'infourl': infourl})


@junit
def cmd_run(cfg):
    global retcode
    runner = skt.runner.getrunner(*cfg.get('runner'))
    retcode = runner.run(cfg.get('buildurl'), cfg.get('krelease'),
                         cfg.get('wait'), uid=cfg.get('uid'))

    idx = 0
    for job in runner.jobs:
        if cfg.get('wait') and cfg.get('junit') is not None:
            runner.dumpjunitresults(job, cfg.get('junit'))
        save_state(cfg, {'jobid_%s' % (idx): job})
        idx += 1

    cfg['jobs'] = runner.jobs

    if retcode != 0 and cfg.get('basehead') and cfg.get('publisher') \
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
        if baseres != 0:
            retcode = 0

    save_state(cfg, {'retcode': retcode})

    if retcode != 0 and cfg.get('bisect'):
        cfg['commitbad'] = cfg.get('mergehead')
        cmd_bisect(cfg)


def cmd_report(cfg):
    if cfg.get("reporter") is None:
        return

    cfg['reporter'][1].update({'cfg': cfg})
    reporter = skt.reporter.getreporter(*cfg.get('reporter'))
    reporter.report()


def cmd_cleanup(cfg):
    config = cfg.get('_parser')
    if config.has_section('state'):
        config.remove_section('state')
        # FIXME Move expansion up the call stack, as this limits the function
        # usefulness, because tilde is a valid path character.
        with open(os.path.expanduser(cfg.get('rc')), 'w') as fp:
            config.write(fp)

    if cfg.get('buildinfo') is not None:
        try:
            os.unlink(cfg.get('buildinfo'))
        except OSError:
            pass

    if cfg.get('tarpkg') is not None:
        try:
            os.unlink(cfg.get('tarpkg'))
        except OSError:
            pass

    if cfg.get('wipe'):
        # FIXME Move expansion up the call stack, as this limits the function
        # usefulness, because tilde is a valid path character.
        shutil.rmtree(os.path.expanduser(cfg.get('builddir')))


def cmd_all(cfg):
    cmd_merge(cfg)
    cmd_build(cfg)
    cmd_publish(cfg)
    cmd_run(cfg)
    if cfg.get('wait'):
        cmd_report(cfg)
    cmd_cleanup(cfg)


@junit
def cmd_bisect(cfg):
    if len(cfg.get('merge_ref')) != 1:
        raise Exception(
            "Bisecting currently works only with exactly one mergeref"
        )

    ktree = skt.ktree(cfg.get('baserepo'),
                      ref=cfg.get('commitgood'),
                      wdir=cfg.get('workdir'))
    head = ktree.checkout()

    cfg['workdir'] = ktree.getpath()
    cfg['buildinfo'] = None

    logging.info("Building good commit: %s", head)
    cmd_build(cfg)
    cmd_publish(cfg)
    os.unlink(cfg.get('tarpkg'))

    runner = skt.runner.getrunner(*cfg.get('runner'))

    retcode = runner.run(cfg.get('buildurl'), cfg.get('krelease'),
                         wait=True, host=cfg.get('host'),
                         uid="[bisect] [good %s]" % head,
                         reschedule=False)

    cfg['host'] = runner.gethost()

    if retcode != 0:
        logging.warning("Good commit %s failed, aborting bisect", head)
        cmd_cleanup(cfg)
        return

    ktree.merge_git_ref(cfg.get('merge_ref')[0][0], cfg.get('commitbad'))
    binfo = ktree.bisect_start(head)

    ret = 0
    while ret == 0:
        cmd_build(cfg)
        cmd_publish(cfg)
        os.unlink(cfg.get('tarpkg'))
        retcode = runner.run(cfg.get('buildurl'), cfg.get('krelease'),
                             wait=True, host=cfg.get('host'),
                             uid="[bisect] [%s]" % binfo,
                             reschedule=False)

        (ret, binfo) = ktree.bisect_iter(retcode)

    cmd_cleanup(cfg)


def addtstamp(path, tstamp):
    return os.path.join(os.path.dirname(path),
                        "%s-%s" % (tstamp, os.path.basename(path)))


def setup_logging(verbose):
    """
    Setup the root logger.

    Args:
        verbose:    Verbosity level to setup log message filtering.
    """
    logging.basicConfig(format="%(asctime)s %(levelname)8s   %(message)s")
    logger.setLevel(logging.WARNING - (verbose * 10))


def setup_parser():
    """
    Create an skt command line parser.

    Returns:
        The created parser.
    """
    parser = argparse.ArgumentParser()

    parser.add_argument("-d", "--workdir", type=str,
                        help="Path to work dir with kernel source git tree",
                        default=os.environ.get("SKT_WORKDIR"))
    parser.add_argument("-b", "--builddir", type=str,
                        help="Path to the build directory (default: WORKDIR)",
                        default=os.environ.get("SKT_BUILDDIR"))
    parser.add_argument("-w", "--wipe",
                        help="Clean build (make mrproper before building), "
                        "remove BUILDDIR when finished",
                        action="store_true", default=False)
    parser.add_argument("--junit",
                        help="Path to dir to store junit results in")
    parser.add_argument("-v", "--verbose", help="Increase verbosity level",
                        action="count", default=0)
    parser.add_argument("--rc", help="Path to rc file", default=DEFAULTRC)
    # FIXME Storing state in config file can break the whole system in case
    #       state saving aborts. It's better to save state separately.
    #       It also breaks separation of concerns, as in principle skt doesn't
    #       need to modify its own configuration otherwise.
    parser.add_argument("--state", help="Save/read state from 'state' section "
                        "of rc file", action="store_true", default=False)

    subparsers = parser.add_subparsers()

    parser_merge = subparsers.add_parser("merge", add_help=False)
    parser_merge.add_argument("-b", "--baserepo", type=str,
                              help="Base repo URL")
    parser_merge.add_argument("--ref", type=str,
                              help="Base repo ref (default: master")
    parser_merge.add_argument("--patchlist", type=str, nargs="+",
                              help="List of patch paths to apply")
    parser_merge.add_argument("--pw", type=str, nargs="+",
                              help="Patchwork urls")
    parser_merge.add_argument("-m", "--merge-ref", nargs="+",
                              help="Merge ref format: 'url [ref]'",
                              action="append")

    parser_build = subparsers.add_parser("build", add_help=False)
    parser_build.add_argument("-c", "--baseconfig", type=str,
                              help="Path to kernel config to use",
                              default=os.environ.get("SKT_BASECONFIG"))
    parser_build.add_argument("--cfgtype", type=str, help="How to process "
                              "default config (default: olddefconfig)")
    parser_build.add_argument("--makeopts", type=str,
                              help="Additional options to pass to make")

    parser_publish = subparsers.add_parser("publish", add_help=False)
    parser_publish.add_argument("-p", "--publisher", type=str, nargs=3,
                                help="Publisher config string in 'type "
                                "destination baseurl' format")
    parser_publish.add_argument("--tarpkg", type=str,
                                help="Path to tar pkg to publish")
    parser_publish.add_argument("--buildinfo", type=str,
                                help="Path to accompanying buildinfo")

    parser_run = subparsers.add_parser("run", add_help=False)
    parser_run.add_argument("-r", "--runner", nargs=2, type=str,
                            help="Runner config in 'type \"{'key' : 'val', "
                            "...}\"' format")
    parser_run.add_argument("--buildurl", type=str, help="Build tarpkg url")
    parser_run.add_argument("--krelease", type=str,
                            help="Kernel release version of the build")
    parser_run.add_argument("--wait", action="store_true", default=False,
                            help="Do not exit until tests are finished")
    parser_run.add_argument("--bisect", help="Try to bisect the failure if "
                            "any.  (Implies --wait)",
                            action="store_true", default=False)

    parser_report = subparsers.add_parser("report", add_help=False)
    parser_report.add_argument("--reporter", nargs=2, type=str,
                               help="Reporter config in 'type \"{'key' : "
                               "'val', ...}\"' format")
    parser_report.set_defaults(func=cmd_report)
    parser_report.set_defaults(_name="report")

    parser_cleanup = subparsers.add_parser("cleanup", add_help=False)

    parser_all = subparsers.add_parser(
        "all",
        parents=[parser_merge, parser_build, parser_publish, parser_run,
                 parser_report, parser_cleanup]
    )

    parser_merge.add_argument("-h", "--help", help="Merge sub-command help",
                              action="help")
    parser_build.add_argument("-h", "--help", help="Build sub-command help",
                              action="help")
    parser_publish.add_argument("-h", "--help", action="help",
                                help="Publish sub-command help")
    parser_run.add_argument("-h", "--help", help="Run sub-command help",
                            action="help")
    parser_report.add_argument("-h", "--help", help="Report sub-command help",
                               action="help")

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

    parser_bisect = subparsers.add_parser("bisect", add_help=True)
    parser_bisect.add_argument("commitbad", type=str,
                               help="Bad commit for bisect")
    parser_bisect.add_argument("--commitgood", type=str, help="Good commit "
                               "for bisect. Default's to baserepo's HEAD")
    parser_bisect.add_argument("--host", type=str, help="If needs to be "
                               "bisected on specific host")
    parser_bisect.set_defaults(func=cmd_bisect)
    parser_bisect.set_defaults(_name="bisect")

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
    config = ConfigParser.ConfigParser()
    config.read(os.path.expanduser(args.rc))
    cfg = vars(args)
    cfg['_parser'] = config
    cfg['_testcases'] = []

    # Read 'state' section first so that it is not overwritten by 'config'
    # section values.
    if cfg.get('state') and config.has_section('state'):
        for (name, value) in config.items('state'):
            if name not in cfg or cfg.get(name) is None:
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
            if name not in cfg or cfg.get(name) is None:
                cfg[name] = value

    if config.has_section('publisher') and ('publisher' not in cfg or
                                            cfg.get('publisher') is None):
        cfg['publisher'] = [config.get('publisher', 'type'),
                            config.get('publisher', 'destination'),
                            config.get('publisher', 'baseurl')]

    if config.has_section('runner') and ('runner' not in cfg or
                                         cfg.get('runner') is None):
        rcfg = {}
        for (key, val) in config.items('runner'):
            if key == 'type':
                continue
            rcfg[key] = val
        cfg['runner'] = [config.get('runner', 'type'), rcfg]
    elif 'runner' in cfg and cfg.get('runner') is not None:
        cfg['runner'] = [cfg.get('runner')[0],
                         ast.literal_eval(cfg.get('runner')[1])]

    if config.has_section('reporter') and (cfg.get('reporter') is None):
        rcfg = {}
        for (key, val) in config.items('reporter'):
            if key == 'type':
                continue
            rcfg[key] = val
        cfg['reporter'] = [config.get('reporter', 'type'), rcfg]
    elif 'reporter' in cfg and cfg.get('reporter') is not None:
        cfg['reporter'] = [cfg.get('reporter')[0],
                           ast.literal_eval(cfg.get('reporter')[1])]

    if 'merge_ref' not in cfg or cfg.get('merge_ref') is None:
        cfg['merge_ref'] = []

    for section in config.sections():
        if section.startswith("merge-"):
            mdesc = [config.get(section, 'url')]
            if config.has_option(section, 'ref'):
                mdesc.append(config.get(section, 'ref'))
            cfg['merge_ref'].append(mdesc)

    if cfg.get("bisect"):
        cfg['wait'] = True

    # Default BUILDDIR = WORKDIR
    if not cfg.get("builddir"):
        cfg["builddir"] = cfg.get("workdir")

    return cfg


def main():
    global retcode

    parser = setup_parser()
    args = parser.parse_args()

    setup_logging(args.verbose)
    cfg = load_config(args)

    args.func(cfg)
    if cfg.get('junit') is not None:
        ts = junit_xml.TestSuite("skt", cfg.get('_testcases'))
        with open("%s/%s.xml" % (cfg.get('junit'), args._name), 'w') as fp:
            junit_xml.TestSuite.to_file(fp, [ts])

    sys.exit(retcode)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        # cleanup??
        print("\nExited at user request.")
        sys.exit(1)
