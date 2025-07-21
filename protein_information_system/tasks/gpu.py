"""
GPU Tasks
=========

GPU tasks are specialized for managing and executing computationally intensive operations using Graphics
Processing Units (GPUs). These tasks are optimized to efficiently handle large-scale data processing,
particularly during model training and inference, where high-performance computing is critical.

**Purpose**

The `GPUTaskInitializer` class extends the `QueueTaskInitializer` to provide the necessary
framework for managing GPU-accelerated tasks. It handles the dynamic loading and unloading of GPU models,
manages worker processes, and ensures that tasks are processed efficiently using GPU resources.

**Customization**

To create a custom GPU-based task, subclass `GPUTaskInitializer` and implement the `enqueue`, `process`, and
 `store_entry` methods. These methods define the logic for enqueuing tasks, processing them using GPUs, and storing the results in the database.

**Key Features**

- **Efficient Resource Management**: Dynamically load and unload GPU models to maximize resource utilization.
- **Scalable Task Execution**: Handle large batches of data, optimizing throughput during model training and inference.
- **Seamless Integration**: Fully integrates with RabbitMQ for distributed task management, ensuring reliable and scalable task execution.
- **Worker Management**: Manages GPU-specific worker processes to handle tasks in parallel.
- **Extensibility**: Abstract methods are provided to allow developers to define custom GPU task logic.

**Example Usage**

Here is an example of how to subclass `GPUTaskInitializer`:

.. code-block:: python

   from protein_information_system.tasks.gpu import GPUTaskInitializer

   class MyCustomGPUTask(GPUTaskInitializer):
       def enqueue(self):
           # Implementation of the task enqueuing logic for GPU processing
           pass

       def process(self, target):
           # Processing logic for the target data using GPU
           pass

       def store_entry(self, record):
           # Logic to store the processed record in the database
           pass
"""

import multiprocessing
import pickle
import threading
from abc import abstractmethod

import pika
from pika import PlainCredentials
from protein_information_system.tasks.queue import QueueTaskInitializer


class GPUTaskInitializer(QueueTaskInitializer):
    """
    The GPUTaskInitializer class extends QueueTaskInitializer to manage tasks
    that are specifically designed for GPU-based processing.

    This class provides the necessary infrastructure for setting up RabbitMQ queues,
    coordinating GPU-specific worker processes, and ensuring that tasks are efficiently
    processed using GPUs.

    Attributes:
        stop_event (multiprocessing.Event): An event to signal workers and threads to stop.
        model_instances (dict): Dictionary storing loaded models for each type.
        tokenizer_instances (dict): Dictionary storing loaded tokenizers for each type.
    """

    def __init__(self, conf, session_required=True):
        """
        Initialize the GPUTaskInitializer.

        This constructor initializes the configuration, and if required, sets up
        a database session. It also prepares the RabbitMQ connection parameters and
        initializes the stop event for managing worker processes.

        Args:
            conf (dict): Configuration dictionary.
            session_required (bool): Whether a database session is required.
                                     If True, the session is initialized.
        """
        super().__init__(conf, session_required)
        self.stop_event = multiprocessing.Event()
        self.model_instances = {}
        self.tokenizer_instances = {}

    def setup_rabbitmq(self):
        """
        Set up RabbitMQ by declaring the necessary queues for GPU tasks.

        This method connects to RabbitMQ using the provided credentials and declares
        the necessary queues for each GPU model type, as well as the queue for data insertion.

        Raises:
            Exception: If there is an issue setting up RabbitMQ.
        """
        try:
            credentials = PlainCredentials(self.conf['rabbitmq_user'], self.conf['rabbitmq_password'])
            self.connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=self.conf['rabbitmq_host'], credentials=credentials))
            self.channel = self.connection.channel()

            # Declare queues for each embedding type
            for model_type in self.conf['embedding']['types']:
                queue_name = f"{self.computing_queue}_{model_type}"
                self.channel.queue_declare(queue=queue_name)
            self.channel.queue_declare(queue=self.inserting_queue)

        except Exception:
            raise

    def start_workers(self):
        """
        Start the worker processes for GPU task processing and database insertion.

        This method spawns worker processes to handle GPU-based task processing
        and inserts the processed data into the database. It also starts a monitoring
        thread to oversee the queues.

        The method ensures that models are loaded and unloaded as needed to optimize GPU usage.
        """
        try:
            self.setup_rabbitmq()

            # Start the database inserter process
            db_inserter_process = multiprocessing.Process(target=self.run_db_inserter_worker, args=(self.stop_event,))
            db_inserter_process.start()
            self.processes.append(db_inserter_process)

            # Start the monitor thread
            monitor_thread = threading.Thread(target=self.monitor_queues)
            monitor_thread.start()
            self.threads.append(monitor_thread)

            # Start the processing for each model type sequentially
            for model_type in self.conf['embedding']['types']:
                self.run_processor_worker_sequential(model_type)

            db_inserter_process.join()

        finally:
            self.cleanup()

    def cleanup(self):
        """
        Clean up resources and stop worker processes.

        This method ensures that all worker processes and threads are properly terminated.
        """
        self.stop_event.set()
        for t in self.threads:
            t.join()
        for p in self.processes:
            p.terminate()  # Ensure all processes are terminated properly

    def run_processor_worker_sequential(self, model_type):
        """
        Run the processor worker sequentially for a specific GPU model type.

        This method manages the loading and unloading of models for each task
        in the GPU processing pipeline, ensuring efficient GPU usage.

        Args:
            model_type (str): The type of GPU model to be used for processing.
        """
        last_model_type = None  # Track the last model type loaded
        with self.create_rabbitmq_connection().channel() as channel:
            queue_name = f"{self.computing_queue}_{model_type}"
            channel.basic_qos(prefetch_count=1)
            while not self.stop_event.is_set():
                method_frame, header_frame, body = channel.basic_get(queue=queue_name, auto_ack=False)
                if method_frame:
                    # Check if model needs to be changed
                    if model_type != last_model_type:
                        if last_model_type is not None:
                            self.unload_model(last_model_type)  # Unload previous model
                        self.load_model(model_type)  # Load the required model
                        last_model_type = model_type

                    # Process task
                    self.callback(channel, method_frame, header_frame, body)
                else:
                    break
            if last_model_type is not None:
                self.unload_model(last_model_type)  # Ensure last model is unloaded

    def load_model(self, model_type):
        """
        Load the GPU model into memory.

        This method loads the specified model and its tokenizer into memory for processing tasks.

        Args:
            model_type (str): The type of model to load.
        """
        type_obj = self.types[model_type]
        module = type_obj['module']
        model = module.load_model(type_obj['model_name'], self.conf)
        tokenizer = module.load_tokenizer(type_obj['model_name'])
        self.model_instances[model_type] = model
        self.tokenizer_instances[model_type] = tokenizer

    def unload_model(self, model_type):
        """
        Unload the GPU model from memory.

        This method removes the specified model and its tokenizer from memory.

        Args:
            model_type (str): The type of model to unload.
        """
        if model_type in self.model_instances:
            del self.model_instances[model_type]
            del self.tokenizer_instances[model_type]

    def publish_task(self, batch_data, model_type):
        """
        Publish a task to the GPU processing queue.

        This method serializes the task data and publishes it to the appropriate
        queue for the specified model type.

        Args:
            batch_data (any): The task data to be processed.
            model_type (str): The type of model for which the task is intended.
        """
        if not isinstance(batch_data, bytes):
            batch_data = pickle.dumps(batch_data)
        if not self.channel or not self.channel.is_open:
            self.setup_rabbitmq()
        queue_name = f"{self.computing_queue}_{model_type}"
        self.channel.basic_publish(exchange='', routing_key=queue_name, body=batch_data)

    # Abstract methods inherited from QueueTaskInitializer
    @abstractmethod
    def enqueue(self):
        pass

    @abstractmethod
    def process(self, target):
        pass

    @abstractmethod
    def store_entry(self, record):
        pass
