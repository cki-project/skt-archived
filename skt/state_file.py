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
"""Functions that manage the skt state file."""
import logging
import os
import yaml


def destroy(cfg):
    """
    Destroy the state file.

    Args:
        cfg: A dictionary of skt configuration
    """
    state_file = cfg.get('state')

    if os.path.isfile(state_file):
        try:
            os.unlink(state_file)
        except IOError as exception:
            logging.error("Failed to delete state file: %s", exception)
            raise exception


def read(cfg):
    """
    Read the state file from the disk.

    Args:
        cfg: A dictionary of skt configuration
    """
    # Return an empty state dictionary if the state file does not exist
    current_state = {}

    state_file = cfg.get('state')
    logging.debug("Reading state from: %s", state_file)

    if os.path.isfile(state_file):
        try:
            with open(state_file, 'r') as fileh:
                current_state = yaml.load(fileh)
        except IOError as exception:
            logging.error("Failed to read state file: %s", exception)
            raise exception

    return current_state


def update(cfg, state_updates):
    """
    Update the state file on disk with new state data.

    Args:
        cfg:           A dictionary of skt configuration.
        state_updates: A dictionary of items to update.
    """
    current_state = read(cfg)

    # Merge the current state and the updates.
    new_state = current_state.copy()
    new_state.update(state_updates)

    # Save the new state to the state file
    logging.debug("Saving state: %s", state_updates)
    state_file = cfg.get('state')

    try:
        with open(state_file, 'w') as fileh:
            yaml_state = yaml.dump(new_state, default_flow_style=False)
            fileh.write(yaml_state)
    except IOError as exception:
        logging.error("Failed to update state file: %s", exception)
        raise exception
