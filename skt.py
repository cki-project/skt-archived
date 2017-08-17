#!/usr/bin/python2

import ConfigParser
import argparse
import ast
import datetime
import logging
import os
import shutil
import sys
import skt, skt.runner, skt.publisher

DEFAULTRC = "~/.sktrc"
logger = logging.getLogger()

def save_state(cfg, state):
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


def cmd_merge(cfg):
    ktree = skt.ktree(cfg.get('baserepo'), ref=cfg.get('ref'),
                              wdir=cfg.get('workdir'))
    ktree.checkout()
    for mb in cfg.get('merge_ref'):
        ktree.merge_git_ref(*mb)

    kpath = ktree.getpath()
    buildinfo = ktree.dumpinfo()

    save_state(cfg, {'workdir'   : kpath,
                     'buildinfo' : buildinfo})

    return (kpath, buildinfo)

def cmd_build(cfg):
    tstamp = datetime.datetime.strftime(datetime.datetime.now(), "%Y%m%d%H%M%S")

    builder = skt.kbuilder(cfg.get('workdir'), cfg.get('baseconfig'),
                                      cfg.get('cfgtype'))

    tgz = builder.mktgz(cfg.get('wipe'))
    ttgz = addtstamp(tgz, tstamp)
    os.rename(tgz, ttgz)
    logging.info("tarball path: %s", ttgz)

    tbuildinfo = None
    if cfg.get('buildinfo') != None:
        tbuildinfo = addtstamp(cfg['buildinfo'], tstamp)
        os.rename(cfg['buildinfo'], tbuildinfo)

    krelease = builder.getrelease()

    save_state(cfg, {'tarpkg'    : ttgz,
                     'buildinfo' : tbuildinfo,
                     'krelease'  : krelease})

    return (ttgz, tbuildinfo, krelease)


def cmd_publish(cfg):
    publisher = skt.publisher.getpublisher(*cfg.get('publisher'))

    url = publisher.publish(cfg['tarpkg'])
    logging.info("published url: %s", url)

    if cfg.get('buildinfo') != None:
        publisher.publish(cfg['buildinfo'])

    save_state(cfg, {'buildurl' : url})

    return url

def cmd_run(cfg):
    runner = skt.runner.getrunner(*cfg.get('runner'))
    runner.run(cfg.get('buildurl'), cfg.get('krelease'), cfg['wait'])

def cmd_cleanup(cfg):
    config = cfg.get('_parser')
    if config.has_section('state'):
        config.remove_section('state')
        with open(os.path.expanduser(cfg.get('rc')), 'w') as fp:
            config.write(fp)

    if cfg.get('buildinfo') != None:
        try:
            os.unlink(cfg['buildinfo'])
        except:
            pass

    if cfg.get('tarpkg') != None:
        try:
            os.unlink(cfg['tarpkg'])
        except:
            pass

    if cfg.get('wipe'):
        shutil.rmtree(os.path.expanduser(cfg.get('workdir')))

def cmd_all(cfg):
    (cfg['workdir'], cfg['buildinfo']) = cmd_merge(cfg)
    (cfg['tarpkg'], cfg['buildinfo'], cfg['krelease']) = cmd_build(cfg)
    cfg['buildurl'] = cmd_publish(cfg)
    cmd_run(cfg)
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
    parser.add_argument("-v", "--verbose", help="Increase verbosity level",
                        action="count", default=0)
    parser.add_argument("--rc", help="Path to rc file", default=DEFAULTRC)
    parser.add_argument("--state", help="Save/read state from 'state' section of rc file",
                        action="store_true", default=False)

    subparsers = parser.add_subparsers()

    parser_merge = subparsers.add_parser("merge", add_help=False)
    parser_merge.add_argument("-b", "--baserepo", type=str, help="Base repo URL")
    parser_merge.add_argument("--ref", type=str, help="Base repo ref (default: master")
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

    parser_cleanup = subparsers.add_parser("cleanup", add_help=False)

    parser_all = subparsers.add_parser("all", parents = [parser_merge,
        parser_build, parser_publish, parser_run, parser_cleanup])

    parser_merge.add_argument("-h", "--help", help="Merge sub-command help",
                              action="help")
    parser_build.add_argument("-h", "--help", help="Build sub-command help",
                              action="help")
    parser_publish.add_argument("-h", "--help", help="Publish sub-command help",
                              action="help")
    parser_run.add_argument("-h", "--help", help="Run sub-command help",
                              action="help")

    parser_merge.set_defaults(func=cmd_merge)
    parser_build.set_defaults(func=cmd_build)
    parser_publish.set_defaults(func=cmd_publish)
    parser_run.set_defaults(func=cmd_run)
    parser_cleanup.set_defaults(func=cmd_cleanup)
    parser_all.set_defaults(func=cmd_all)

    return parser

def load_config(args):
    config = ConfigParser.ConfigParser()
    config.read(os.path.expanduser(args.rc))
    cfg = vars(args)
    cfg['_parser'] = config

    # Read 'state' section first so that it is not overwritten by 'config'
    # section values.
    if cfg.get('state') and config.has_section('state'):
        for (name, value) in config.items('state'):
            if name not in cfg or cfg[name] == None:
                cfg[name] = value

    if config.has_section('config'):
        for (name, value) in config.items('config'):
            if name not in cfg or cfg[name] == None:
                cfg[name] = value

    if config.has_section('publisher') and ('publisher' not in cfg or
                                            cfg['publisher'] == None):
        cfg['publisher'] = [config.get('publisher', 'type'),
                            config.get('publisher', 'destination'),
                            config.get('publisher', 'baseurl')]

    if config.has_section('runner') and ('runner' not in cfg or
                                            cfg['runner'] == None):
        rcfg = {}
        for (key, val) in config.items('runner'):
            if key == 'type':
                continue
            rcfg[key] = val
        cfg['runner'] = [config.get('runner', 'type'), rcfg]
    elif 'runner' in cfg and cfg['runner'] != None:
        cfg['runner'] = [cfg['runner'][0],
                         ast.literal_eval(cfg['runner'][1])]

    if 'merge_ref' not in cfg or cfg['merge_ref'] == None:
        cfg['merge_ref'] = []

    for section in config.sections():
        if section.startswith("merge-"):
            mdesc = [config.get(section, 'url')]
            if config.has_option(section, 'ref'):
                mdesc.append(config.get(section, 'ref'))
            cfg['merge_ref'].append(mdesc)

    return cfg


def main():

    parser = setup_parser()
    args = parser.parse_args()

    setup_logging(args.verbose)
    cfg = load_config(args)

    args.func(cfg)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        #cleanup??
        print("\nExited at user request.")
        sys.exit(1)
