# Copyright (c) 2018 - 2020 Red Hat, Inc. All rights reserved. This copyrighted
# material is made available to anyone wishing to use, modify, copy, or
# redistribute it subject to the terms and conditions of the GNU General Public
# License v.2 or later.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
"""Constants for reporter."""
# Set a list of stages that will be searched for pipeline data. We start at
# the end and work backwards since the last stage in the pipeline should have
# the most complete set of data.
SKIP_STAGE = 'skip'
TEST_STAGE = 'test'
SETUP_STAGE = 'setup'
PUBLISH_STAGE = 'publish'
BUILD_STAGE = 'build'
CREATEREPO_STAGE = 'createrepo'
MERGE_STAGE = 'merge'
LINT_STAGE = 'lint'
JOB_STAGES = [SKIP_STAGE, TEST_STAGE, SETUP_STAGE, PUBLISH_STAGE, BUILD_STAGE,
              CREATEREPO_STAGE, MERGE_STAGE, LINT_STAGE]
