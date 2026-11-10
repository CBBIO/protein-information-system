"""Unit tests for QueueTaskInitializer error handling (no RabbitMQ or database needed)."""
import logging
import multiprocessing

import pytest

from protein_information_system.tasks.queue import QueueTaskInitializer, TaskFailuresError


class DummyTask(QueueTaskInitializer):
    def enqueue(self):
        pass

    def process(self, target):
        return target

    def store_entry(self, record):
        pass


def make_task(**conf):
    """Build a task without running __init__, which connects to RabbitMQ and the DB."""
    task = DummyTask.__new__(DummyTask)
    task.conf = {"rabbitmq_host": "localhost", "rabbitmq_user": "guest", "rabbitmq_password": "guest",
                 "task_retry_delay": 0, **conf}
    task.logger = logging.getLogger("test_queue_task_initializer")
    task.stop_event = multiprocessing.Event()
    task.failed_tasks = multiprocessing.Value("i", 0)
    task.skipped_tasks = multiprocessing.Value("i", 0)
    task.processes = []
    task.threads = []
    task.setup_rabbitmq = lambda: None
    task.start_workers = lambda: None
    return task


# ---------------------------------------------------------------------------
# Post-processing errors
# ---------------------------------------------------------------------------

def test_post_processing_error_is_raised_when_pipeline_succeeds():
    task = make_task()

    def failing_post_processing():
        raise RuntimeError("summary failed")

    task.post_processing = failing_post_processing

    with pytest.raises(RuntimeError, match="summary failed"):
        task.start()
    assert task.stop_event.is_set()


def test_pipeline_error_is_not_masked_by_post_processing_error():
    task = make_task()

    def failing_workers():
        raise ValueError("worker crashed")

    def failing_post_processing():
        raise RuntimeError("summary failed")

    task.start_workers = failing_workers
    task.post_processing = failing_post_processing

    with pytest.raises(ValueError, match="worker crashed"):
        task.start()


def test_post_processing_runs_after_pipeline_error():
    task = make_task()
    calls = []

    def failing_workers():
        raise ValueError("worker crashed")

    task.start_workers = failing_workers
    task.post_processing = lambda: calls.append("post")

    with pytest.raises(ValueError):
        task.start()
    assert calls == ["post"]


def test_worker_start_errors_reach_start(monkeypatch):
    """Use the real start_workers: a worker that cannot be started must fail start()."""
    import protein_information_system.tasks.queue as queue_module

    class UnstartableProcess:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise OSError("cannot fork")

    task = make_task(max_workers=1)
    del task.start_workers  # back to the class implementation
    task.post_processing = lambda: None
    monkeypatch.setattr(queue_module.multiprocessing, "Process", UnstartableProcess)

    with pytest.raises(OSError, match="cannot fork"):
        task.start()


def test_successful_pipeline_runs_post_processing():
    task = make_task()
    calls = []
    task.post_processing = lambda: calls.append("post")

    task.start()

    assert calls == ["post"]


# ---------------------------------------------------------------------------
# Computing-queue retries
# ---------------------------------------------------------------------------

class FakeChannel:
    def __init__(self):
        self.acks = []
        self.nacks = []
        self.published = []

    def basic_ack(self, delivery_tag):
        self.acks.append(delivery_tag)

    def basic_nack(self, delivery_tag, requeue):
        self.nacks.append((delivery_tag, requeue))

    def basic_publish(self, exchange, routing_key, body):
        self.published.append((routing_key, body))


class Method:
    def __init__(self, delivery_tag=1, redelivered=False):
        self.delivery_tag = delivery_tag
        self.redelivered = redelivered


def _failing_process(target):
    raise RuntimeError("model crashed")


def test_failed_message_is_retried_once():
    import pickle
    task = make_task()
    task.process = _failing_process
    ch = FakeChannel()

    task.callback(ch, Method(redelivered=False), None, pickle.dumps({"x": 1}))

    assert ch.nacks == [(1, True)]


def test_retry_waits_task_retry_delay(monkeypatch):
    import pickle
    import protein_information_system.tasks.queue as queue_module

    slept = []
    monkeypatch.setattr(queue_module.time, "sleep", slept.append)
    task = make_task(task_retry_delay=7)
    task.process = _failing_process
    ch = FakeChannel()

    task.callback(ch, Method(redelivered=False), None, pickle.dumps({"x": 1}))

    assert slept == [7.0]
    assert ch.nacks == [(1, True)]


def test_failed_message_is_discarded_after_retry():
    import pickle
    task = make_task()
    task.process = _failing_process
    ch = FakeChannel()

    task.callback(ch, Method(redelivered=True), None, pickle.dumps({"x": 1}))

    assert ch.nacks == [(1, False)]


def test_successful_message_is_published_and_acked():
    import pickle
    task = make_task()
    ch = FakeChannel()

    task.callback(ch, Method(), None, pickle.dumps({"x": 1}))

    assert ch.acks == [1]
    assert ch.published[0][0] == task.inserting_queue


# ---------------------------------------------------------------------------
# Failed tasks
# ---------------------------------------------------------------------------

def _failing_store_entry(record):
    raise RuntimeError("cannot store")


def _workers_with_two_discarded_tasks(task):
    import pickle

    def workers():
        ch = FakeChannel()
        task.callback(ch, Method(redelivered=False), None, pickle.dumps(1))  # retried: not a failure
        task.callback(ch, Method(redelivered=True), None, pickle.dumps(1))  # discarded
        task.db_inserter_callback(ch, Method(), None, pickle.dumps(1))  # discarded

    return workers


def test_discarded_tasks_fail_the_pipeline_after_post_processing_when_enabled():
    task = make_task(fail_on_task_errors=True)
    calls = []
    task.post_processing = lambda: calls.append("post")
    task.process = _failing_process
    task.store_entry = _failing_store_entry
    task.start_workers = _workers_with_two_discarded_tasks(task)

    with pytest.raises(TaskFailuresError, match="2 failure"):
        task.start()
    assert calls == ["post"]


def test_discarded_tasks_are_only_logged_by_default(caplog):
    task = make_task()
    task.post_processing = lambda: None
    task.process = _failing_process
    task.store_entry = _failing_store_entry
    task.start_workers = _workers_with_two_discarded_tasks(task)

    with caplog.at_level(logging.ERROR):
        task.start()
    assert "2 failure(s)" in caplog.text


def test_failure_count_is_logged_when_post_processing_fails(caplog):
    task = make_task(fail_on_task_errors=True)
    task.process = _failing_process
    task.store_entry = _failing_store_entry
    task.start_workers = _workers_with_two_discarded_tasks(task)

    def failing_post_processing():
        raise RuntimeError("summary failed")

    task.post_processing = failing_post_processing

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="summary failed"):
        task.start()
    assert "2 failure(s)" in caplog.text


def test_counters_are_reset_on_each_start():
    task = make_task(fail_on_task_errors=True)
    task.post_processing = lambda: None
    task.failed_tasks.value = 5
    task.skipped_tasks.value = 5

    task.start()  # no failures in this run

    assert task.failed_tasks.value == 0
    assert task.skipped_tasks.value == 0


def test_value_error_in_process_is_counted_as_a_skip(caplog):
    import pickle
    task = make_task(fail_on_task_errors=True)
    task.post_processing = lambda: None

    def skip(target):
        raise ValueError("nothing to do")

    task.process = skip

    def workers():
        ch = FakeChannel()
        task.callback(ch, Method(), None, pickle.dumps(1))
        assert ch.acks == [1]

    task.start_workers = workers

    with caplog.at_level(logging.WARNING):
        task.start()  # skips do not fail the pipeline
    assert task.failed_tasks.value == 0
    assert "1 task(s) were skipped" in caplog.text


class _ExitedProcess:
    def __init__(self, exitcode):
        self.exitcode = exitcode
        self.name = "Process-1"


def test_crashed_worker_is_counted_as_a_failure():
    task = make_task()
    task._check_worker_exit(_ExitedProcess(-9))  # e.g. killed by the OOM killer
    task._check_worker_exit(_ExitedProcess(0))
    assert task.failed_tasks.value == 1


def _increment_in_child(task):
    task._record_task_failure()


def test_failure_counter_is_shared_with_worker_processes():
    ctx = multiprocessing.get_context("fork")  # make_task() holds lambdas, which spawn cannot pickle
    task = make_task()
    procs = [ctx.Process(target=_increment_in_child, args=(task,)) for _ in range(3)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    assert task.failed_tasks.value == 3


# ---------------------------------------------------------------------------
# delete_queues
# ---------------------------------------------------------------------------

def test_delete_task_queues_only_deletes_own_queues(monkeypatch):
    import protein_information_system.tasks.queue as queue_module

    task = make_task()
    own = [task.computing_queue, task.inserting_queue, f"{task.computing_queue}_ESM"]
    others = ["othertask_computing", "someone_elses_queue", f"x{task.computing_queue}"]
    api = [{"name": n, "vhost": "/"} for n in own + others]
    api.append({"name": task.computing_queue, "vhost": "other_vhost"})
    task._get_all_queues_from_api = lambda: api

    deleted = []

    class Response:
        status_code = 204
        text = ""

    def fake_delete(url, auth):
        deleted.append(url)
        return Response()

    monkeypatch.setattr(queue_module.requests, "delete", fake_delete)

    task.delete_task_queues()

    assert sorted(u.rsplit("/", 1)[1] for u in deleted) == sorted(own)
    assert all("/%2F/" in u for u in deleted)


def test_monitor_only_watches_own_queues():
    task = make_task()
    task.monitor_interval = 0
    # Another task whose queue name contains ours as a substring keeps messages forever.
    task._get_all_queues_from_api = lambda: [
        {"name": task.computing_queue, "vhost": "/", "messages_ready": 0, "messages_unacknowledged": 0},
        {"name": f"{task.computing_queue}_ESM", "vhost": "/", "messages_ready": 0, "messages_unacknowledged": 0},
        {"name": f"my{task.computing_queue}", "vhost": "/", "messages_ready": 3, "messages_unacknowledged": 0},
    ]

    task.monitor_queues()  # returns once our own queues are empty

    assert task.stop_event.is_set()
