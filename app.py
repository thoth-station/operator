#!/usr/bin/env python3
# thoth-graph-sync-scheduler
# Copyright(C) 2018, 2019 Fridolin Pokorny
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

"""Scheduler handling Thoth's graph syncs."""

import sys
import os
import logging
from multiprocessing import Queue
from multiprocessing import Process

import click

from thoth.common import init_logging
from thoth.common import OpenShift
from thoth.common import __version__ as thoth_common_version

init_logging()

_LOGGER = logging.getLogger("thoth.graph_sync_scheduler")
_INFRA_NAMESPACE = os.environ["THOTH_INFRA_NAMESPACE"]


# TODO: move scheduler configuration out of sources
# Mapping from source job to destination job, boolean flag states if failed jobs should be synced as well.
_CONFIG = {
    "adviser": (OpenShift.schedule_graph_sync_adviser, False),
    "package-analyzer": (OpenShift.schedule_graph_sync_package_analyzer, False),
    "inspection": (OpenShift.schedule_graph_sync_inspection, True),
    "provenance-checker": (OpenShift.schedule_graph_sync_provenance_checker, False),
    "solver": (OpenShift.schedule_graph_sync_solver, False),
    "package-extract": (OpenShift.schedule_graph_sync_package_extract, False),
    "dependency-monkey": (OpenShift.schedule_graph_sync_dependency_monkey, False),
}


def event_producer(queue: Queue, scheduler_namespace: str):
    """Listen for relevant events in the cluster and schedule them for further processing by parent process."""
    # Note that jobs do not support field selector pointing to phase (we could
    # do it on pod level, but that is not desired).
    openshift = OpenShift()
    v1_jobs = openshift.ocp_client.resources.get(api_version="batch/v1", kind="Job")
    for event in v1_jobs.watch(namespace=scheduler_namespace, label_selector="operator=graph-sync"):
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

        task_name = event["object"].metadata.labels.task
        _LOGGER.info("Handling event for %r (task: %r)", event["object"].metadata.name, task_name)

        target = _CONFIG.get(task_name)
        if not target:
            _LOGGER.error("No configuration entry provided for task %r in graph sync operator", task_name)
            continue

        method, sync_failed = target

        if not sync_failed and event["object"].status.failed:
            _LOGGER.info(
                "Skipping failed job %r as scheduler was not configured to perform sync on failed jobs",
                event["object"].metadata.name
            )
            continue

        # Document id directly matches job name.
        document_id = event["object"].metadata.name
        queue.put((method.__name___name, document_id))


@click.command()
@click.option(
    "--verbose",
    is_flag=True,
    envvar="THOTH_SCHEDULER_VERBOSE",
    help="Be verbose about what is going on.",
)
@click.option(
    "--scheduler-namespace",
    type=str,
    required=True,
    envvar="THOTH_SCHEDULER_NAMESPACE",
    help="Namespace to connect to to wait for events.",
)
@click.option(
    "--graph-sync-namespace",
    type=str,
    required=True,
    envvar="THOTH_GRAPH_SYNC_NAMESPACE",
    help="Namespace in which graph syncs should be run.",
)
def cli(scheduler_namespace: str, graph_sync_namespace: str, verbose: bool = False):
    """Scheduler handling Thoth's graph syncs."""
    if verbose:
        _LOGGER.setLevel(logging.DEBUG)

    _LOGGER.info(
        "Graph sync scheduler is running thoth-common in version %r, built from %r",
        thoth_common_version,
        os.getenv("OPENSHIFT_BUILD_COMMIT")
    )
    _LOGGER.info(
        "Graph sync scheduler is watching namespace %r and scheduling graph syncs in namespace %r",
        scheduler_namespace, graph_sync_namespace
    )

    openshift = OpenShift()

    queue = Queue()
    producer = Process(target=event_producer, args=(queue, scheduler_namespace))

    producer.start()
    while producer.is_alive():
        method_name, document_id = queue.get()

        try:
            method = getattr(OpenShift, method_name)
            graph_sync_id = method(
                openshift,
                document_id,
                namespace=graph_sync_namespace,
            )
            _LOGGER.info("Scheduled new graph sync with id %r", graph_sync_id)
        except Exception as exc:
            _LOGGER.exception(
                "Failed to run sync for document id %r, the method to be triggered was %r: %s",
                document_id,
                method_name,
                exc
            )

    producer.join()

    # Always fail, this should be run forever.
    sys.exit(1)


if __name__ == "__main__":
    sys.exit(cli())
