"""
Queue Tasks
=============

Queue tasks distribute work through RabbitMQ: ``enqueue()`` publishes tasks to a
computing queue, worker processes run ``process()`` and publish the results to an
inserting queue, and a database worker runs ``store_entry()``. When the queues are
drained, ``post_processing()`` runs once.

**Error handling**

- ``process()`` raising ``ValueError`` means "skip this task": the message is
  acknowledged and the number of skipped tasks is reported at the end.
- Any other error in ``process()``: the task is retried once after
  ``task_retry_delay`` seconds (default 5). If it fails again it is discarded. A
  message that was already redelivered (for example after a worker crash) is
  discarded on its first failure.
- An error in ``store_entry()``: the record is discarded.
- Discarded tasks and worker processes that exit abnormally are counted. At the end
  of ``start()`` they are logged; with ``fail_on_task_errors: True`` a
  :class:`TaskFailuresError` is raised instead. Post-processing always runs first,
  so partial results are written.
- Errors while starting or joining workers, and errors in ``post_processing()``,
  are raised by ``start()``. A post-processing error never hides an earlier
  pipeline error.

**Queues**

Each task uses ``<task>_computing``, ``<task>_inserting`` and, for GPU tasks,
``<task>_computing_<model>``, where ``<task>`` is the lower-cased class name. With
``delete_queues: True`` these queues, and only these, are deleted at start-up.
Two runs of the same task on one broker share these queues and should not run
concurrently.
"""

import multiprocessing
import pickle
import threading
import time
from abc import abstractmethod
import pika
import requests
from pika import PlainCredentials

from protein_information_system.tasks.base import BaseTaskInitializer


class TaskFailuresError(RuntimeError):
    """Raised when tasks were discarded or worker processes crashed during the pipeline."""


class QueueTaskInitializer(BaseTaskInitializer):
    """
    The QueueTaskInitializer class extends BaseTaskInitializer to manage tasks
    that are distributed and processed via RabbitMQ queues.

    It provides the necessary infrastructure to configure RabbitMQ, coordinate
    worker processes, and ensure that tasks are handled efficiently and reliably.

    Attributes:
        processes (list): A list of multiprocessing.Process objects representing the worker processes.
        threads (list): A list of threading.Thread objects for auxiliary tasks like monitoring queues.
        stop_event (multiprocessing.Event): An event to signal workers and threads to stop.
        connection_params (pika.ConnectionParameters): Parameters for connecting to the RabbitMQ server.
        failed_tasks (multiprocessing.Value): Number of tasks discarded after failing plus worker
            processes that exited abnormally, shared with the worker processes.
        skipped_tasks (multiprocessing.Value): Number of tasks skipped because ``process()``
            raised ``ValueError``.
    """

    def __init__(self, conf, session_required=True):
        """
        Initialize the QueueTaskInitializer.

        This constructor initializes the configuration, and if required, sets up
        a database session. It also prepares the RabbitMQ connection parameters and
        initializes the processes and threads lists.

        Args:
            conf (dict): Configuration dictionary.
            session_required (bool): Whether a database session is required.
                                     If True, the session is initialized.
        """
        super().__init__(conf, session_required)
        self.processes = []
        self.threads = []
        self.stop_event = multiprocessing.Event()
        self.failed_tasks = multiprocessing.Value('i', 0)
        self.skipped_tasks = multiprocessing.Value('i', 0)
        self.monitor_interval = conf.get('monitor_interval', 30)
        self.connection_params = pika.ConnectionParameters(
            host=self.conf['rabbitmq_host'],
            port=self.conf.get('rabbitmq_port', 5672),
            heartbeat=0,
            credentials=PlainCredentials(
                self.conf['rabbitmq_user'],
                self.conf['rabbitmq_password']
            )
        )
        self.logger.info("QueueTaskInitializer has been successfully initialized.")
        if self.conf.get('delete_queues', False):
            self.delete_task_queues()

        self.connection = self.create_rabbitmq_connection()
        self.channel = self.connection.channel()

    def setup_rabbitmq(self):
        """
        Set up RabbitMQ by declaring the necessary queues.
        """
        try:
            self.logger.info("Setting up RabbitMQ.")
            credentials = PlainCredentials(
                self.conf['rabbitmq_user'],
                self.conf['rabbitmq_password']
            )
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=self.conf['rabbitmq_host'],
                    port=self.conf.get('rabbitmq_port', 5672),
                    credentials=credentials
                )
            )
            channel = connection.channel()
            channel.queue_declare(queue=self.computing_queue, durable=True)
            channel.queue_declare(queue=self.inserting_queue, durable=True)
            connection.close()

            self.logger.info(
                f"Queues declared: {self.computing_queue}, {self.inserting_queue}"
            )
        except Exception as e:
            self.logger.error(
                f"Error while setting up RabbitMQ: {e}",
                exc_info=True
            )
            raise

    @property
    def computing_queue(self):
        """
        Property to get the name of the computing queue.
        """
        return f'{self.__class__.__name__.lower()}_computing'

    @property
    def inserting_queue(self):
        """
        Property to get the name of the inserting queue.
        """
        return f'{self.__class__.__name__.lower()}_inserting'

    def start(self):
        """
        Start the task processing pipeline:
        - Initialize RabbitMQ
        - Enqueue tasks
        - Launch workers and wait for completion
        - Run post-processing hook

        Raises:
            Exception: Any error from the pipeline. An error from the post-processing
                hook is raised when the pipeline itself succeeded; if the pipeline
                failed, it is logged and the pipeline error is raised instead.
            TaskFailuresError: If ``fail_on_task_errors`` is true and tasks were discarded
                or a worker process exited abnormally. Raised after post-processing, so
                partial results are still written. When it is false (the default), the
                failures are only logged.
        """
        self.logger.info("Starting the task processing pipeline.")
        self._reset_counters()
        completed = False
        try:
            self.setup_rabbitmq()
            self.logger.info("RabbitMQ set up. Enqueueing tasks…")
            self.enqueue()
            self.logger.info("Tasks enqueued. Starting workers…")
            self.start_workers()
            self.logger.info("All workers finished. Running post-processing hook…")
            completed = True
        except Exception as e:
            self.logger.error("Error in task processing pipeline: %s", e, exc_info=True)
            raise
        finally:
            self.stop_event.set()
            self.logger.info("Stop event has been set.")
            try:
                self.post_processing()
                self.logger.info("Post-processing hook completed.")
            except Exception as e:
                self.logger.error("Post-processing hook failed: %s", e, exc_info=True)
                # Only surface it when the pipeline itself succeeded; otherwise the
                # original pipeline error is already propagating.
                if completed:
                    self._report_task_outcome(raise_on_failures=False)
                    raise

        self._report_task_outcome()

    @staticmethod
    def _increment(counter):
        """Increment a shared counter, if present (safe across worker processes)."""
        if counter is not None:
            with counter.get_lock():
                counter.value += 1

    def _reset_counters(self):
        for name in ('failed_tasks', 'skipped_tasks'):
            counter = getattr(self, name, None)
            if counter is not None:
                with counter.get_lock():
                    counter.value = 0

    def _record_task_failure(self):
        """Count a task discarded after failing, or a worker that exited abnormally."""
        self._increment(getattr(self, 'failed_tasks', None))

    def _record_task_skip(self):
        """Count a task skipped because ``process()`` raised ``ValueError``."""
        self._increment(getattr(self, 'skipped_tasks', None))

    def _check_worker_exit(self, process):
        """Record a failure if a joined worker process exited abnormally (crash, OOM kill, signal)."""
        if process.exitcode not in (0, None):
            self.logger.error(
                "Worker process %s exited with code %s; its in-flight work may be lost.",
                process.name, process.exitcode
            )
            self._record_task_failure()

    def _report_task_outcome(self, raise_on_failures=True):
        """Log skipped tasks and failures; raise TaskFailuresError if configured to."""
        skipped = getattr(self, 'skipped_tasks', None)
        if skipped is not None and skipped.value:
            self.logger.warning(
                "%d task(s) were skipped because process() raised ValueError; see the warnings above.",
                skipped.value
            )
        failed = getattr(self, 'failed_tasks', None)
        if failed is None or not failed.value:
            return
        message = (
            f"{failed.value} failure(s): tasks discarded after failing or worker processes "
            "that exited abnormally; see the errors above."
        )
        if raise_on_failures and self.conf.get('fail_on_task_errors', False):
            raise TaskFailuresError(message)
        self.logger.error(message)

    def start_workers(self):
        """
        Start worker processes for task computing and database insertion.
        """
        self.logger.info("Starting computing and inserting workers.")
        try:
            for i in range(self.conf['max_workers']):
                p = multiprocessing.Process(
                    target=self.run_processor_worker,
                    args=(self.stop_event,)
                )
                p.start()
                self.processes.append(p)
                self.logger.info(f"Started computing worker {i + 1}.")

            p = multiprocessing.Process(
                target=self.run_db_inserter_worker,
                args=(self.stop_event,)
            )
            p.start()
            self.processes.append(p)
            self.logger.info("Started database insertion worker.")

            monitor_thread = threading.Thread(target=self.monitor_queues)
            monitor_thread.start()
            self.threads.append(monitor_thread)
            self.logger.info("Started queue monitoring thread.")

            for proc in self.processes:
                proc.join()
                self.logger.info("A worker process has finished.")
                self._check_worker_exit(proc)
        except Exception as e:
            self.logger.error(
                f"Error during worker execution: {e}",
                exc_info=True
            )
            raise
        finally:
            self.stop_event.set()
            for t in self.threads:
                t.join()
            self.logger.info("All workers and threads have been stopped.")

    def run_processor_worker(self, stop_event):
        """
        Run the processor worker: consumes messages and processes tasks.
        """
        self.logger.info("Processor worker started.")
        with self.create_rabbitmq_connection() as connection:
            channel = connection.channel()
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(
                queue=self.computing_queue,
                on_message_callback=self.callback
            )
            self.consume_messages(channel, stop_event)
        self.logger.info("Processor worker stopped.")

    def run_db_inserter_worker(self, stop_event):
        """
        Run the database inserter worker: consumes messages and inserts them into DB.
        """
        self.logger.info("Database insertion worker started.")
        with self.create_rabbitmq_connection() as connection:
            channel = connection.channel()
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(
                queue=self.inserting_queue,
                on_message_callback=self.db_inserter_callback
            )
            self.consume_messages(channel, stop_event)
        self.logger.info("Database insertion worker stopped.")

    def create_rabbitmq_connection(self):
        """
        Create a new RabbitMQ connection.
        """
        self.logger.debug("Creating a new RabbitMQ connection.")
        return pika.BlockingConnection(self.connection_params)

    def consume_messages(self, channel, stop_event):
        """
        Consume messages from the RabbitMQ queue until the stop event is set.
        """
        self.logger.debug("Starting message consumption loop.")
        while not stop_event.is_set():
            try:
                channel.connection.process_data_events(time_limit=1)
            except pika.exceptions.ConnectionClosedByBroker:
                self.logger.warning("Connection closed by the broker.")
                break
            except pika.exceptions.AMQPChannelError as e:
                self.logger.error(f"AMQPChannelError encountered: {e}")
                break
            except pika.exceptions.AMQPConnectionError as e:
                self.logger.error(f"AMQPConnectionError encountered: {e}")
                break

    def monitor_queues(self):
        """
        Dynamically monitors available queues and filters them based on defined patterns.
        Sends a stop signal if all relevant queues are empty.
        """
        self.logger.info("Queue monitoring thread started.")
        while not self.stop_event.is_set():
            time.sleep(self.monitor_interval)
            try:
                # Consultar todas las colas a través de la API de RabbitMQ
                all_queues = self._get_all_queues_from_api()
                if not all_queues:
                    self.logger.warning("No queues found from RabbitMQ API.")
                    continue

                relevant_queues = self._task_queues(all_queues)

                if not relevant_queues:
                    self.logger.info("No relevant queues found. Stopping monitoring.")
                    self.stop_event.set()
                    break

                all_queues_empty = all(
                    not queue.get('messages_ready', 0) and not queue.get('messages_unacknowledged', 0)

                    for queue in relevant_queues
                )

                if all_queues_empty:
                    self.logger.info("All relevant queues are empty. Sending stop signal.")
                    self.stop_event.set()
                    break

            except Exception as e:
                self.logger.error(f"Error during queue monitoring: {e}", exc_info=True)
                self.stop_event.set()

    def publish_task(self, data):
        """
        Publish a task to the computing queue.
        """
        self.logger.debug("Publishing a task to the computing queue.")
        if not isinstance(data, bytes):
            data = pickle.dumps(data)

            self.channel.basic_publish(
                exchange='',
                routing_key=self.computing_queue,
                body=data
            )
        self.logger.debug("Task published successfully.")

    def db_inserter_callback(self, ch, method, properties, body):
        """
        Handle messages received in the inserting queue, storing them in the DB.
        """
        self.logger.debug("Message received on the inserting queue.")
        try:
            record = pickle.loads(body)
            self.store_entry(record)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            self.logger.debug("Message stored and acknowledged.")
        except Exception as e:
            self.logger.error(
                f"Error while processing message in the inserting queue: {e}",
                exc_info=True
            )
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            self._record_task_failure()

    def callback(self, ch, method, properties, body):
        """
        Handle messages received in the computing queue, process them, and optionally
        publish to the inserting queue.
        """
        self.logger.debug("Message received on the computing queue.")
        try:
            data = pickle.loads(body)
            record = self.process(data)

            if record:
                record_bytes = pickle.dumps(record)
                ch.basic_publish(
                    exchange='',
                    routing_key=self.inserting_queue,
                    body=record_bytes
                )
                self.logger.debug("Record published to the inserting queue.")

            ch.basic_ack(delivery_tag=method.delivery_tag)
            self.logger.debug("Message acknowledged.")
        except ValueError as e:
            # ValueError from process() means "skip this task" (kept for backward
            # compatibility). Skips are counted and reported at the end of start().
            self.logger.warning(
                f"ValueError while processing message: {e}. Acknowledging without publishing."
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)
            self._record_task_skip()
        except Exception as e:
            # Retry once, after task_retry_delay seconds so that a brief outage of a
            # remote service can recover. A message that fails again is discarded:
            # requeueing it forever loops on permanent errors (with basic_get it is
            # fetched again immediately). A message redelivered after a consumer crash
            # also counts as a retry.
            if getattr(method, "redelivered", False):
                self.logger.error(
                    f"Error while processing message in the computing queue after a retry: {e}. "
                    "Discarding the message.",
                    exc_info=True
                )
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                self._record_task_failure()
            else:
                delay = float(self.conf.get('task_retry_delay', 5))
                self.logger.error(
                    f"Error while processing message in the computing queue: {e}. "
                    f"Retrying once in {delay:g} s.",
                    exc_info=True
                )
                time.sleep(delay)
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

    @abstractmethod
    def enqueue(self):
        """
        Abstract method to enqueue tasks. Must be overridden by subclasses.
        """
        pass

    @abstractmethod
    def process(self, target):
        """
        Abstract method to process tasks. Must be overridden by subclasses.
        """
        pass

    @abstractmethod
    def store_entry(self, record):
        """
        Abstract method to store processed entries. Must be overridden by subclasses.
        """
        pass

    def _get_all_queues_from_api(self):
        """
        Recupera la lista de todas las colas desde la API HTTP de RabbitMQ.

        Returns
        -------
        list
            Lista de diccionarios con información de las colas.
        """
        from requests.auth import HTTPBasicAuth

        rabbitmq_host = self.conf.get('rabbitmq_host', 'localhost')
        rabbitmq_port_http = self.conf.get('rabbitmq_port_http', 15672)
        rabbitmq_user = self.conf.get('rabbitmq_user', 'guest')
        rabbitmq_password = self.conf.get('rabbitmq_password', 'guest')

        url = f"http://{rabbitmq_host}:{rabbitmq_port_http}/api/queues"
        auth = HTTPBasicAuth(rabbitmq_user, rabbitmq_password)

        try:
            response = requests.get(url, auth=auth)
            response.raise_for_status()
            return response.json()  # Devuelve una lista de colas
        except requests.RequestException as e:
            self.logger.error(f"Failed to fetch queues from RabbitMQ API: {e}")
            return []

    def is_task_queue(self, queue_name):
        """
        Return True if ``queue_name`` belongs to this task: its computing and inserting
        queues, and the per-model computing queues (``<computing_queue>_<model>``).
        """
        if queue_name in (self.computing_queue, self.inserting_queue):
            return True
        return queue_name.startswith(f"{self.computing_queue}_")

    def _task_queues(self, queues):
        """Filter queue dicts from the RabbitMQ API to this task's queues in the connection's vhost."""
        params = getattr(self, 'connection_params', None)
        vhost = params.virtual_host if params is not None else '/'
        return [
            q for q in queues
            if self.is_task_queue(q['name']) and q.get('vhost', '/') == vhost
        ]

    def delete_task_queues(self):
        """
        Delete this task's queues (see :meth:`is_task_queue`) through the RabbitMQ HTTP API.

        Used when ``delete_queues`` is enabled. Queues of other tasks, applications or
        users on the same broker are left untouched.
        """
        queues = self._task_queues(self._get_all_queues_from_api())
        if not queues:
            self.logger.info("No queues of this task to delete.")
            return
        self._delete_queues(queues)

    def delete_all_queues(self):
        """
        Deletes all queues from the RabbitMQ server using the HTTP API.

        This removes every queue on the broker, including those of other applications
        and users. ``delete_queues`` uses :meth:`delete_task_queues` instead.

        Raises
        ------
        Exception
            If an error occurs during queue deletion.
        """
        # Obtener la lista de todas las colas
        queues = self._get_all_queues_from_api()
        if not queues:
            self.logger.info("No queues to delete.")
            return
        self._delete_queues(queues)

    def _delete_queues(self, queues):
        """Delete the given queues (dicts from the RabbitMQ API) through the HTTP API."""
        from requests.auth import HTTPBasicAuth

        rabbitmq_port_http = self.conf.get('rabbitmq_port_http', 15672)
        url_base = f"http://{self.conf['rabbitmq_host']}:{rabbitmq_port_http}/api/queues"

        auth = HTTPBasicAuth(self.conf['rabbitmq_user'], self.conf['rabbitmq_password'])

        for queue in queues:
            queue_name = queue['name']
            vhost = queue['vhost']
            encoded_vhost = vhost.replace("/", "%2F")  # Escapar '/' en el vhost
            queue_url = f"{url_base}/{encoded_vhost}/{queue_name}"

            try:
                response = requests.delete(queue_url, auth=auth)
                if response.status_code == 204:
                    self.logger.info(f"Queue '{queue_name}' deleted successfully.")
                else:
                    self.logger.warning(
                        f"Failed to delete queue '{queue_name}': {response.status_code} {response.text}"
                    )
            except requests.RequestException as e:
                self.logger.error(f"Error deleting queue '{queue_name}': {e}")

    def post_processing(self) -> None:
        """
        Finalization hook executed after all workers have finished and queues are drained.
        Subclasses may override to run post-processing steps (e.g., consolidations, reports).
        Default: no-op.
        """
        pass
