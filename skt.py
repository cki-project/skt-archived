#!/usr/bin/python2

# Copyright (c) 2017 Red Hat, Inc. All rights reserved. This copyrighted material
# is made available to anyone wishing to use, modify, copy, or
# redistribute it subject to the terms and conditions of the GNU General
# Public License v.2 or later.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

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
import skt, skt.runner, skt.publisher, skt.reporter

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
        if val != None:
            logging.debug("state: %s -> %s", key, val)
            config.set('state', key, val)

    with open(os.path.expanduser(cfg.get('rc')), 'w') as fp:
        config.write(fp)

def junit(func):
    def wrapper(cfg):
        global retcode
        if cfg.get('junit') != None:
            tstart = time.time()
            tc = junit_xml.TestCase(func.__name__, classname="skt")

            try:
                func(cfg)
            except Exception as e:
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
    ktree = skt.ktree(cfg.get('baserepo'), ref=cfg.get('ref'),
                              wdir=cfg.get('workdir'))
    bhead = ktree.checkout()
    commitdate = ktree.get_commit_date(bhead)
    save_state(cfg, {'baserepo' : cfg.get('baserepo'),
                     'basehead' : bhead,
                     'commitdate' : commitdate})

    try:
        idx = 0
        for mb in cfg.get('merge_ref'):
            (retcode, head) = ktree.merge_git_ref(*mb)
            save_state(cfg, {'meregerepo_%02d' % idx : mb[0],
                             'mergehead_%02d' % idx : head})

            utypes.append("[git]")
            idx += 1
            if retcode != 0:
                return

        if cfg.get('patchlist') != None:
            utypes.append("[local patch]")
            idx = 0
            for patch in cfg.get('patchlist'):
                save_state(cfg, {'localpatch_%02d' % idx : patch})
                ktree.merge_patch_file(patch)
                idx += 1

        if cfg.get('pw') != None:
            utypes.append("[patchwork]")
            idx = 0
            for patch in cfg.get('pw'):
                ktree.merge_patchwork_patch(patch)
                save_state(cfg, {'patchwork_%02d' % idx : patch})
                idx += 1
    except Exception as e:
        save_state(cfg, {'mergelog' : ktree.mergelog})
        raise e

    uid = "[baseline]"
    if len(utypes):
        uid = " ".join(utypes)

    kpath = ktree.getpath()
    buildinfo = ktree.dumpinfo()
    buildhead = ktree.get_commit()

    save_state(cfg, {'workdir'   : kpath,
                     'buildinfo' : buildinfo,
                     'buildhead' : buildhead,
                     'uid'       : uid})

@junit
def cmd_build(cfg):
    tstamp = datetime.datetime.strftime(datetime.datetime.now(), "%Y%m%d%H%M%S")

    builder = skt.kbuilder(cfg.get('workdir'), cfg.get('baseconfig'),
                                      cfg.get('cfgtype'))

    try:
        tgz = builder.mktgz(cfg.get('wipe'))
    except Exception as e:
        save_state(cfg, {'buildlog' : builder.buildlog})
        raise e

    if cfg.get('buildhead') != None:
        ttgz = "%s.tar.gz" % cfg.get('buildhead')
    else:
        ttgz = addtstamp(tgz, tstamp)
    os.rename(tgz, ttgz)
    logging.info("tarball path: %s", ttgz)

    tbuildinfo = None
    if cfg.get('buildinfo') != None:
        if cfg.get('buildhead') != None:
            tbuildinfo = "%s.csv" % cfg.get('buildhead')
        else:
            tbuildinfo = addtstamp(cfg.get('buildinfo'), tstamp)
        os.rename(cfg.get('buildinfo'), tbuildinfo)

    tconfig = "%s.config" % cfg.get('buildinfo', tstamp)
    shutil.copyfile(builder.get_cfgpath(), tconfig)

    krelease = builder.getrelease()

    save_state(cfg, {'tarpkg'    : ttgz,
                     'buildinfo' : tbuildinfo,
                     'buildconf' : tconfig,
                     'krelease'  : krelease})

@junit
def cmd_publish(cfg):
    publisher = skt.publisher.getpublisher(*cfg.get('publisher'))

    infourl = None
    url = publisher.publish(cfg.get('tarpkg'))
    logging.info("published url: %s", url)

    if cfg.get('buildinfo') != None:
        infourl = publisher.publish(cfg.get('buildinfo'))

    if cfg.get('buildconf') != None:
        cfgurl = publisher.publish(cfg.get('buildconf'))

    save_state(cfg, {'buildurl' : url,
                     'cfgurl'   : cfgurl,
                     'infourl'  : infourl})

@junit
def cmd_run(cfg):
    global retcode
    runner = skt.runner.getrunner(*cfg.get('runner'))
    retcode = runner.run(cfg.get('buildurl'), cfg.get('krelease'),
                         cfg.get('wait'), uid = cfg.get('uid'))

    idx = 0
    for job in runner.jobs:
        if cfg.get('wait') and cfg.get('junit') != None:
            runner.dumpjunitresults(job, cfg.get('junit'))
        save_state(cfg, {'jobid_%s' % (idx) : job})
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
                                 host = basehost, uid = "baseline check",
                                 reschedule = False)
        save_state(cfg, {'baseretcode' : baseres})

        # If baseline also fails - assume pass
        if baseres != 0:
            retcode = 0

    save_state(cfg, {'retcode' : retcode})

    if retcode != 0 and cfg.get('bisect') == True:
        cfg['commitbad'] = cfg.get('mergehead')
        cmd_bisect(cfg)

def cmd_report(cfg):
    if cfg.get("reporter") == None:
        return

    cfg['reporter'][1].update({'cfg' : cfg})
    reporter = skt.reporter.getreporter(*cfg.get('reporter'))
    reporter.report()

def cmd_cleanup(cfg):
    config = cfg.get('_parser')
    if config.has_section('state'):
        config.remove_section('state')
        with open(os.path.expanduser(cfg.get('rc')), 'w') as fp:
            config.write(fp)

    if cfg.get('buildinfo') != None:
        try:
            os.unlink(cfg.get('buildinfo'))
        except:
            pass

    if cfg.get('tarpkg') != None:
        try:
            os.unlink(cfg.get('tarpkg'))
        except:
            pass

    if cfg.get('wipe'):
        shutil.rmtree(os.path.expanduser(cfg.get('workdir')))

def cmd_all(cfg):
    cmd_merge(cfg)
    cmd_build(cfg)
    cmd_publish(cfg)
    cmd_run(cfg)
    if cfg.get('wait') == True:
        cmd_report(cfg)
    cmd_cleanup(cfg)

@junit
def cmd_bisect(cfg):
    if len(cfg.get('merge_ref')) != 1:
        raise Exception("Bisecting currently works only with exactly one mergeref")

    ktree = skt.ktree(cfg.get('baserepo'), ref=cfg.get('commitgood'),
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
                         wait = True, host = cfg.get('host'),
                         uid = "[bisect] [good %s]" % head,
                         reschedule = False)

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
                             wait = True, host = cfg.get('host'),
                             uid = "[bisect] [%s]" % binfo,
                             reschedule = False)

        (ret, binfo) = ktree.bisect_iter(retcode)

    cmd_cleanup(cfg)

def addtstamp(path, tstamp):
    return os.path.join(os.path.dirname(path),
                        "%s-%s" % (tstamp, os.path.basename(path)))

def setup_logging(verbose):
    logging.basicConfig(format="%(asctime)s %(levelname)8s   %(message)s")
    logger.setLevel(logging.WARNING - (verbose * 10))


def setup_parser():
    parser = argparse.ArgumentParser()

    parser.add_argument("-d", "--workdir", type=str, help="Path to work dir")
    parser.add_argument("-w", "--wipe", help="Clean build (make mrproper before building), remove workdir when finished",
                        action="store_true", default=False)
    parser.add_argument("--junit", help="Path to dir to store junit results in")
    parser.add_argument("-v", "--verbose", help="Increase verbosity level",
                        action="count", default=0)
    parser.add_argument("--rc", help="Path to rc file", default=DEFAULTRC)
    parser.add_argument("--state", help="Save/read state from 'state' section of rc file",
                        action="store_true", default=False)

    subparsers = parser.add_subparsers()

    parser_merge = subparsers.add_parser("merge", add_help=False)
    parser_merge.add_argument("-b", "--baserepo", type=str, help="Base repo URL")
    parser_merge.add_argument("--ref", type=str, help="Base repo ref (default: master")
    parser_merge.add_argument("--patchlist", type=str, nargs="+", help="List of patch paths to apply")
    parser_merge.add_argument("--pw", type=str, nargs="+", help="Patchwork urls")
    parser_merge.add_argument("-m", "--merge-ref", nargs="+", help="Merge ref format: 'url [ref]'",
                              action="append")

    parser_build = subparsers.add_parser("build", add_help=False)
    parser_build.add_argument("-c", "--baseconfig", type=str, help="Path to kernel config to use")
    parser_build.add_argument("--cfgtype", type=str, help="How to process default config (default: olddefconfig)")

    parser_publish = subparsers.add_parser("publish", add_help=False)
    parser_publish.add_argument("-p", "--publisher", type=str, nargs=3, help="Publisher config string in 'type destination baseurl' format")
    parser_publish.add_argument("--tarpkg", type=str, help="Path to tar pkg to publish")
    parser_publish.add_argument("--buildinfo", type=str, help="Path to accompanying buildinfo")

    parser_run = subparsers.add_parser("run", add_help=False)
    parser_run.add_argument("-r", "--runner", nargs=2, type=str, help="Runner config in 'type \"{'key' : 'val', ...}\"' format")
    parser_run.add_argument("--buildurl", type=str, help="Build tarpkg url")
    parser_run.add_argument("--krelease", type=str, help="Kernel release version of the build")
    parser_run.add_argument("--wait", help="Do not exit until tests are finished",
                            action="store_true", default=False)
    parser_run.add_argument("--bisect", help="Try to bisect the failure if any.  (Implies --wait)",
                            action="store_true", default=False)

    parser_report = subparsers.add_parser("report", add_help=False)
    parser_report.add_argument("--reporter", nargs=2, type=str, help="Reporter config in 'type \"{'key' : 'val', ...}\"' format")
    parser_report.set_defaults(func=cmd_report)
    parser_report.set_defaults(_name="report")

    parser_cleanup = subparsers.add_parser("cleanup", add_help=False)

    parser_all = subparsers.add_parser("all", parents = [parser_merge,
        parser_build, parser_publish, parser_run, parser_report,
        parser_cleanup])

    parser_merge.add_argument("-h", "--help", help="Merge sub-command help",
                              action="help")
    parser_build.add_argument("-h", "--help", help="Build sub-command help",
                              action="help")
    parser_publish.add_argument("-h", "--help", help="Publish sub-command help",
                              action="help")
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
    parser_bisect.add_argument("commitbad", type=str, help="Bad commit for bisect")
    parser_bisect.add_argument("--commitgood", type=str, help="Good commit for bisect. Default's to baserepo's HEAD")
    parser_bisect.add_argument("--host", type=str, help="If needs to be bisected on specific host")
    parser_bisect.set_defaults(func=cmd_bisect)
    parser_bisect.set_defaults(_name="bisect")

    return parser

def load_config(args):
    config = ConfigParser.ConfigParser()
    config.read(os.path.expanduser(args.rc))
    cfg = vars(args)
    cfg['_parser'] = config
    cfg['_testcases'] = []

    # Read 'state' section first so that it is not overwritten by 'config'
    # section values.
    if cfg.get('state') and config.has_section('state'):
        for (name, value) in config.items('state'):
            if name not in cfg or cfg.get(name) == None:
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
            if name not in cfg or cfg.get(name) == None:
                cfg[name] = value

    if config.has_section('publisher') and ('publisher' not in cfg or
                                            cfg.get('publisher') == None):
        cfg['publisher'] = [config.get('publisher', 'type'),
                            config.get('publisher', 'destination'),
                            config.get('publisher', 'baseurl')]

    if config.has_section('runner') and ('runner' not in cfg or
                                            cfg.get('runner') == None):
        rcfg = {}
        for (key, val) in config.items('runner'):
            if key == 'type':
                continue
            rcfg[key] = val
        cfg['runner'] = [config.get('runner', 'type'), rcfg]
    elif 'runner' in cfg and cfg.get('runner') != None:
        cfg['runner'] = [cfg.get('runner')[0],
                         ast.literal_eval(cfg.get('runner')[1])]

    if config.has_section('reporter') and (cfg.get('reporter') == None):
        rcfg = {}
        for (key, val) in config.items('reporter'):
            if key == 'type':
                continue
            rcfg[key] = val
        cfg['reporter'] = [config.get('reporter', 'type'), rcfg]
    elif 'reporter' in cfg and cfg.get('reporter') != None:
        cfg['reporter'] = [cfg.get('reporter')[0],
                         ast.literal_eval(cfg.get('reporter')[1])]


    if 'merge_ref' not in cfg or cfg.get('merge_ref') == None:
        cfg['merge_ref'] = []

    for section in config.sections():
        if section.startswith("merge-"):
            mdesc = [config.get(section, 'url')]
            if config.has_option(section, 'ref'):
                mdesc.append(config.get(section, 'ref'))
            cfg['merge_ref'].append(mdesc)

    if cfg.get("bisect"):
        cfg['wait'] = True

    return cfg


def main():
    global retcode

    parser = setup_parser()
    args = parser.parse_args()

    setup_logging(args.verbose)
    cfg = load_config(args)

    args.func(cfg)
    if cfg.get('junit') != None:
        ts = junit_xml.TestSuite("skt", cfg.get('_testcases'))
        with open("%s/%s.xml" % (cfg.get('junit'), args._name), 'w') as fp:
            junit_xml.TestSuite.to_file(fp, [ts])

    sys.exit(retcode)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        #cleanup??
        print("\nExited at user request.")
        sys.exit(1)
