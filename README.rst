Thoth's Graph Sync Scheduler
----------------------------

OpenShift event checker for scheduling Thoth's graph syncs.


Basic Usage
===========

This scheduler is capable of triggering jobs based on success of previous jobs.
The operator listens in a configured namespace for events that have label
``operator=graph-sync`` set and are job successful runs. Scheduler then checks label
with key ``task`` - the value present under this key is used as a "task name"
stated in the configuration which states what template should be used to run a
subsequent job.

Basically the current implementation is capable of chaining jobs (the chained one is
graph sync) based on a success of job that computes results.
