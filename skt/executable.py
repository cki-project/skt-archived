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

import configparser
import ast
import atexit
import logging
import os
import signal
import sys
import tempfile

import argparse
from skt.runner import BeakerRunner

LOGGER = logging.getLogger()


def full_path(path):
    """Get an absolute path to a file."""
    return os.path.abspath(os.path.expanduser(path))


def save_state(cfg, state):
    """
    Merge state to cfg, and then save cfg.

    Args:
        cfg:    A dictionary of skt configuration.
        state:  A dictionary of skt current state.
    """

    for (key, val) in state.items():
        cfg[key] = val

    if not cfg.get('state'):
        return

    config = cfg.get('_parser')
    if not config.has_section("state"):
        config.add_section("state")

    for (key, val) in state.items():
        if val is not None:
            logging.debug("state: %s -> %s", key, val)
            config.set('state', key, str(val))

    with open(cfg.get('rc'), 'w') as fileh:
        config.write(fileh)


def cmd_run(cfg):
    """
    Run tests on a built kernel using the specified "runner". Only "Beaker"
    runner is currently supported.

    Args:
        cfg:    A dictionary of skt configuration.
    """
    runner_config = cfg.get('runner')
    runner_config = [x for x in runner_config if isinstance(x, dict)][0]

    runner = BeakerRunner(**runner_config)

    atexit.register(runner.cleanup_handler)
    signal.signal(signal.SIGINT, runner.signal_handler)
    signal.signal(signal.SIGTERM, runner.signal_handler)
    retcode = runner.run(cfg.get('kernel_package_url'),
                         cfg.get('max_aborted_count'),
                         cfg.get('kernel_version'),
                         cfg.get('wait'),
                         arch=cfg.get("kernel_arch"),
                         waiving=cfg.get('waiving'))

    recipe_set_index = 0
    for index, job in enumerate(runner.job_to_recipe_set_map.keys()):
        save_state(cfg, {'jobid_%s' % (index): job})
        for recipe_set in runner.job_to_recipe_set_map[job]:
            save_state(cfg,
                       {'recipesetid_%s' % (recipe_set_index): recipe_set})
            recipe_set_index += 1

    cfg['jobs'] = runner.job_to_recipe_set_map.keys()

    save_state(cfg, {'retcode': retcode})

    return retcode


def setup_logging(verbose):
    """
    Setup the root logger.

    Args:
        verbose:    Verbosity level to setup log message filtering.
    """
    logging.basicConfig(format="%(asctime)s %(levelname)8s   %(message)s")
    LOGGER.setLevel(logging.WARNING - (verbose * 10))
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


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
        "-o",
        "--output-dir",
        type=str,
        help="Path to output directory"
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
        required=True
    )
    parser.add_argument(
        "--waiving",
        help=(
            "Hide waived tests."
        ),
        type=lambda x: (str(x).lower() == 'true'),
        default=True
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

    # These arguments apply to the 'run' skt command
    parser_run = subparsers.add_parser("run", add_help=False)
    parser_run.add_argument(
        '--max-aborted-count',
        type=int,
        help='Ignore <count> aborted jobs to work around temporary '
        + 'infrastructure issues. Defaults to 3.'
    )
    parser_run.add_argument(
        "--wait",
        action="store_true",
        default=False,
        help="Do not exit until tests are finished"
    )

    parser_run.add_argument(
        "-h",
        "--help",
        help="Run sub-command help",
        action="help"
    )

    parser_run.set_defaults(func=cmd_run)
    parser_run.set_defaults(_name="run")

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
    config_parser = configparser.RawConfigParser()
    config_parser.read(os.path.abspath(args.rc))

    cfg = vars(args)


    # Get an absolute path for the work directory
    if cfg.get('workdir'):
        cfg['workdir'] = full_path(cfg.get('workdir'))
    else:
        cfg['workdir'] = tempfile.mkdtemp()

    # Get an absolute path for the configuration file
    cfg['rc'] = full_path(cfg.get('rc'))

    # Get absolute paths to state files for multireport
    # Handle "result" being None if none are specified
    for idx, statefile_path in enumerate(cfg.get('result') or []):
        cfg['result'][idx] = full_path(statefile_path)

    # Assign default max aborted count if it's not defined in config file
    if not cfg.get('max_aborted_count'):
        cfg['max_aborted_count'] = 3

    # Get absolute path to blacklist file
    if cfg.get('runner') and cfg['runner'][0] == 'beaker' and \
            'blacklist' in cfg['runner'][1]:
        cfg['runner'][1]['blacklist'] = full_path(
            cfg['runner'][1]['blacklist']
        )

    # Create and get an absolute path for the output directory
    if cfg.get('output_dir'):
        cfg['output_dir'] = full_path(cfg.get('output_dir'))
        try:
            os.mkdir(cfg.get('output_dir'))
        except OSError:
            pass
    elif os.access(cfg.get('workdir'), os.W_OK | os.X_OK):
        cfg['output_dir'] = cfg.get('workdir')
    else:
        cfg['output_dir'] = os.getcwd()

    return cfg


def main():
    """This is the main entry point used by setup.cfg."""
    # pylint: disable=protected-access
    try:
        parser = setup_parser()
        args = parser.parse_args()

        setup_logging(args.verbose)

        # We are gradually migrating away from messing with cfg and passing
        # it everywhere.
        cfg = load_config(args)
        retcode = args.func(cfg)

        sys.exit(retcode)
    except KeyboardInterrupt:
        print("\nExited at user request.")
        sys.exit(130)
