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
import configparser
import os


def get_state(state_file, state_key):
    """
    Read and return a value from the state file for a specified key.

    Args:
        state_file: Path to a state file.
        state_key:  The key for a desired value in the state file.

    Returns:
        Value from the state file for the corresponding key. The value type
        will be the same as what was set when the value was stored.

    """
    config = configparser.RawConfigParser()

    # Does this state file exist?
    if not os.path.isfile(state_file):
        return None

    # Read the state file
    config.read(state_file)

    # Check if the state file has a 'state' section.
    if not config.has_section("state"):
        return None

    # Get the value from the state file.
    try:
        return config.get('state', state_key)
    except configparser.NoOptionError:
        return None


def update_state(state_file, state_dict):
    """
    Write updated state information to the state file.

    Args:
        state_file: Path to state file.
        state_dict: A dictionary of key/value pairs to update in statefile.
    """
    config = configparser.RawConfigParser()

    # If the state file exists, read its current values.
    if os.path.isfile(state_file):
        config.read(state_file)

    # Add a 'state' section if it doesn't exist already.
    if not config.has_section("state"):
        config.add_section("state")

    # Iterate over the state_dict and update key/value pairs.
    for (key, val) in state_dict.items():
        config.set('state', key, val)

    # Write the update state file to disk.
    with open(state_file, 'w') as fileh:
        config.write(fileh)
