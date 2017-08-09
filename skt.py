#!/usr/bin/python2

import ConfigParser
import argparse
import ast
import datetime
import logging
import os
import sys
import skt, skt.runner, skt.publisher

def addtstamp(path, tstamp):
    return os.path.join(os.path.dirname(path),
                        "%s-%s" % (tstamp, os.path.basename(path)))

def main():
    tstamp = datetime.datetime.strftime(datetime.datetime.now(), "%Y%m%d%H%M%S")
    parser = argparse.ArgumentParser()

    parser.add_argument("-b", "--baserepo", type=str, help="Base repo URL")
    parser.add_argument("--branch", type=str, help="Base repo branch (default: master")
    parser.add_argument("--cfgtype", type=str, help="How to process default config (default: olddefconfig)")
    parser.add_argument("-c", "--baseconfig", type=str, help="Path to kernel config to use")
    parser.add_argument("-d", "--workdir", type=str, help="Path to work dir")
    parser.add_argument("-r", "--runner", nargs=2, type=str, help="Runner config in 'type \"{'key' : 'val', ...}\"' format")
    parser.add_argument("-p", "--publisher", type=str, nargs=3, help="Publisher config string in 'type destination baseurl' format")
    parser.add_argument("-w", "--wipe", help="Clean build (make mrproper before building), remove workdir when finished", action="store_true", default=False)
    parser.add_argument("-m", "--merge-branch", nargs="+", help="Merge branch format: 'url [branch]'", action="append")
    parser.add_argument("-v", "--verbose", help="Increase verbosity level", action="count", default=0)
    parser.add_argument("--rc", help="Path to rc file", default="~/.sktrc")

    args = parser.parse_args()

    config = ConfigParser.ConfigParser()
    config.read(os.path.expanduser(args.rc))
    cfg = vars(args)

    logging.basicConfig(format="%(asctime)s %(levelname)8s   %(message)s")
    logger = logging.getLogger()
    logger.setLevel(logging.WARNING - (args.verbose * 10))

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

    if cfg['merge_branch'] == None:
        cfg['merge_branch'] = []

    for section in config.sections():
        if section.startswith("merge-"):
            mdesc = [config.get(section, 'url')]
            if config.has_option(section, 'branch'):
                mdesc.append(config.get(section, 'branch'))
            cfg['merge_branch'].append(mdesc)

    ktree = skt.ktree(cfg.get('baserepo'), branch=cfg.get('branch'),
                              wdir=cfg.get('workdir'))

    ktree.checkout()

    for mb in cfg.get('merge_branch'):
        ktree.merge_git_branch(*mb)

    builder = skt.kbuilder(ktree.getpath(), cfg.get('baseconfig'),
                                      cfg.get('cfgtype'))

    tgz = builder.mktgz(cfg.get('wipe'))
    ttgz = addtstamp(tgz, tstamp)
    os.rename(tgz, ttgz)

    logging.info("tarball path: %s", ttgz)

    infopath = ktree.dumpinfo()
    tinfopath = addtstamp(infopath, tstamp)
    os.rename(infopath, tinfopath)

    publisher = skt.publisher.getpublisher(*cfg.get('publisher'))

    url = publisher.publish(ttgz)
    logging.info("published url: %s", url)

    publisher.publish(tinfopath)

    runner = skt.runner.getrunner(*cfg.get('runner'))
    runner.run(builder.getrelease(), url, str(tstamp))

    if cfg.get('wipe'):
        builder.cleanup()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        #cleanup??
        print("\nExited at user request.")
        sys.exit(1)
