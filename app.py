#!/usr/bin/env python3
# thoth-graph-sync-operator
# Copyright(C) 2018 Fridolin Pokorny
#
# This program is free software: you can redistribute it and / or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""Operator handling Thoth's graph syncs."""

import sys
import os
import logging

import click
from kubernetes import client
from kubernetes import config
from openshift.dynamic import DynamicClient

from thoth.common import init_logging
from thoth.common import OpenShift

init_logging()

_LOGGER = logging.getLogger("thoth.graph_sync_operator")
_OPENSHIFT = OpenShift()
_INFRA_NAMESPACE = os.environ["THOTH_INFRA_NAMESPACE"]

# TODO: move operator configuration out of sources
# Mapping from source job to destination job, boolean flag states if failed jobs should be synced as well.
_CONFIG = {
    "solver": ("graph-sync-job-solver", False),
    "inspection": ("graph-sync-job-inspection", True),
    "package-extract": ("graph-sync-job-package-extract", False),
}


@click.command()
@click.option(
    "--verbose",
    is_flag=True,
    envvar="THOTH_OPERATOR_VERBOSE",
    help="Be verbose about what is going on.",
)
@click.option(
    "--operator-namespace",
    type=str,
    required=True,
    envvar="THOTH_OPERATOR_NAMESPACE",
    help="Namespace to connect to to wait for events.",
)
@click.option(
    "--graph-sync-namespace",
    type=str,
    required=True,
    envvar="THOTH_GRAPH_SYNC_NAMESPACE",
    help="Namespace in which graph syncs should be run.",
)
def cli(operator_namespace: str, graph_sync_namespace: str, verbose: bool = False):
    """Operator handling Thoth's graph syncs."""
    if verbose:
        _LOGGER.setLevel(logging.DEBUG)

    _LOGGER.info(
        "Graph sync operator is watching namespace %r and scheduling graph syncs in namespace %r",
        operator_namespace, graph_sync_namespace
    )

    config.load_incluster_config()
    dyn_client = DynamicClient(client.ApiClient(configuration=client.Configuration()))
    v1_jobs = dyn_client.resources.get(api_version="batch/v1", kind="Job")

    # Note that jobs do not support field selector pointing to phase (we could
    # do it on pod level, but that is not desired).
    for event in v1_jobs.watch(
        namespace=operator_namespace, label_selector="operator=graph-sync"
    ):
        _LOGGER.debug("Checking event for %r", event["object"].metadata.name)
        if event["type"] != "MODIFIED":
            # Skip additions and deletions...
            _LOGGER.debug("Skipping event, not modification event: %s", event["type"])
            continue

        if not event["object"].status.succeeded and not event["object"].status.failed:
            # Skip modified events signalizing pod being scheduled.
            # We also check for success of failed - the monitored jobs are
            # configured to run once - if they fail they are not restarted.
            # Thus continue on failed.
            _LOGGER.debug("Skipping event, no succeeded nor failed in status reported: %s", event["object"].status)
            continue

        _LOGGER.info("Handling event for %r", event["object"].metadata.name)

        # Document id directly matches job name.
        document_id = event["object"].metadata.name
        task_name = event["object"].metadata.labels.task

        target = _CONFIG.get(task_name)
        template_name, sync_failed = target
        if not template_name:
            _LOGGER.error(
                "No template name defined to be used as a job for task %r", task_name
            )
            continue

        if not sync_failed and event["object"].status.failed:
            _LOGGER.info(
                "Skipping failed job %r as operator was not configured to perform sync on failed jobs",
                event["object"].metadata.name
            )
            continue

        try:
            graph_sync_id = _OPENSHIFT.schedule_graph_sync(
                document_id,
                graph_sync_namespace,
                template_name=template_name
            )
            _LOGGER.info("Scheduled new graph sync with id %r", graph_sync_id)
        except Exception as exc:
            _LOGGER.exception(
                "Failed to run sync for document id %r, the template to be triggered was %r: %s",
                document_id,
                template_name,
                exc
            )


if __name__ == "__main__":
    sys.exit(cli())
