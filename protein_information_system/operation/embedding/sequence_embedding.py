import importlib
import traceback

from protein_information_system.sql.model.entities.embedding.sequence_embedding import (
    SequenceEmbeddingType,
    SequenceEmbedding,
)

from protein_information_system.sql.model.entities.sequence.sequence import Sequence
from protein_information_system.tasks.gpu import GPUTaskInitializer
import os

os.environ["TOKENIZERS_PARALLELISM"] = "true"


class SequenceEmbeddingManager(GPUTaskInitializer):
    """
    Manages the sequence embedding process, including model loading, task enqueuing, and result storing.

    This class initializes GPU tasks, retrieves model configuration, and processes batches of sequences
    for embedding generation.

    Attributes:
        reference_attribute (str): Name of the attribute used as the reference for embedding (default: 'sequence').
        model_instances (dict): Dictionary of loaded models keyed by embedding type ID.
        tokenizer_instances (dict): Dictionary of loaded tokenizers keyed by embedding type ID.
        base_module_path (str): Base module path for dynamic imports of embedding tasks.
        batch_size (int): Number of sequences processed per batch. Defaults to 40.
        types (dict): Configuration dictionary for embedding types.
    """

    def __init__(self, conf):
        """
        Initializes the SequenceEmbeddingManager.

        :param conf: Configuration dictionary containing embedding parameters.
        :type conf: dict

        Example:
            >>> conf = {
            >>>     "embedding": {"batch_size": 50, "types": [1, 2]},
            >>>     "limit_execution": 100
            >>> }
            >>> manager = SequenceEmbeddingManager(conf)
        """
        super().__init__(conf)
        self.reference_attribute = 'sequence'
        self.model_instances = {}
        self.tokenizer_instances = {}
        self.base_module_path = 'protein_information_system.operation.embedding.proccess.sequence'
        self.queue_batch_size = self.conf['embedding'].get('queue_batch_size', 40)
        self.types = self.fetch_models_info()
        self.types_by_id = {v['id']: v for v in self.types.values()}

    def fetch_models_info(self):
        """
        Retrieves and initializes embedding models based on the database configuration.

        :raises sqlalchemy.exc.SQLAlchemyError: If there's an error querying the database.
        """
        self.session_init()
        embedding_types = self.session.query(SequenceEmbeddingType).all()
        self.session.close()
        del self.engine

        types = {}

        for type_obj in embedding_types:
            model_conf = self.conf['embedding']['models']
            if (type_obj.name in model_conf and model_conf[type_obj.name]['enabled'] is True):
                module_name = f"{self.base_module_path}.{type_obj.task_name}"
                module = importlib.import_module(module_name)

                batch_size = self.conf['embedding']['models'][type_obj.name].get('batch_size', 1)

                types[type_obj.name] = {
                    'name': type_obj.name,
                    'module': module,
                    'model_name': type_obj.model_name,
                    'id': type_obj.id,
                    'task_name': type_obj.task_name,
                    'batch_size': batch_size
                }

        return types

    def enqueue(self):
        """
        Enqueues sequence embedding tasks for processing.

        This method retrieves all sequences from the database and filters them according
        to the maximum allowed sequence length (if configured) and an optional execution limit.
        It then organizes the sequences into batches and publishes embedding tasks to the appropriate
        models if no existing embedding is found for a given model and sequence.

        Configuration parameters used:
            - ``embedding.max_sequence_length`` (int, optional): Maximum length allowed for a sequence.
              Sequences exceeding this length are excluded.
            - ``limit_execution`` (int or False): Limits the number of sequences processed.

        Steps:
            1. Retrieve sequences from the database.
            2. Filter out sequences longer than the configured maximum length.
            3. Optionally limit the number of sequences using ``limit_execution``.
            4. Split sequences into batches of size ``queue_batch_size``.
            5. For each sequence-model pair, check for existing embeddings.
            6. If no embedding exists, enqueue the task for processing.

        :raises Exception: If an error occurs during the enqueueing process.
        """
        try:
            self.logger.info("Starting embedding enqueue process.")
            self.session_init()
            sequences = self.session.query(Sequence).all()

            max_length = self.conf['embedding'].get('max_sequence_length')
            if max_length:
                sequences = [s for s in sequences if s.sequence and len(s.sequence) <= max_length]

            if self.conf['limit_execution']:
                sequences = sequences[:self.conf['limit_execution']]

            sequence_batches = [
                sequences[i: i + self.queue_batch_size]
                for i in range(0, len(sequences), self.queue_batch_size)
            ]

            for batch in sequence_batches:
                model_batches = {}

                for sequence in batch:
                    for model_name, type in self.types.items():
                        existing_embedding = self.session.query(SequenceEmbedding).filter_by(
                            sequence_id=sequence.id, embedding_type_id=type['id']
                        ).first()

                        if not existing_embedding:
                            task_data = {
                                'sequence': sequence.sequence,
                                'sequence_id': sequence.id,
                                'model_name': type['model_name'],
                                'embedding_type_id': type['id'],

                            }
                            model_batches.setdefault(model_name, []).append(task_data)

                for model_name, batch_data in model_batches.items():
                    if batch_data:
                        self.publish_task(batch_data, model_name)
                        self.logger.info(
                            f"Published batch with {len(batch_data)} sequences to model '{model_name}' (type ID {self.types[model_name]['id']})."
                        )

            self.session.close()

        except Exception as e:
            self.logger.error(f"Error during enqueue process: {e}")
            raise

    def process(self, batch_data):
        """
        Processes a batch of sequences to generate embeddings.

        :param batch_data: List of dictionaries, each containing sequence data.
        :type batch_data: list[dict]
        :return: List of dictionaries with embedding results.
        :rtype: list[dict]
        :raises Exception: If there's an error during embedding generation.

        Example:
            >>> batch_data = [{"sequence": "ATCG", "sequence_id": 1, "embedding_type_id": 2}]
            >>> results = manager.process(batch_data)
        """
        try:
            embedding_type_id = batch_data[0]['embedding_type_id']
            model_type = self.types_by_id[embedding_type_id]['name']
            model = self.model_instances[model_type]
            tokenizer = self.tokenizer_instances[model_type]
            module = self.types[model_type]['module']

            device = self.conf['embedding'].get('device', "cuda")

            batch_size = self.types[model_type]["batch_size"]

            embedding_records = module.embedding_task(
                sequences=batch_data,
                model=model,
                tokenizer=tokenizer,
                device=device,
                batch_size=batch_size,
                embedding_type_id=embedding_type_id
            )

            return embedding_records

        except Exception as e:
            self.logger.error(f"Error during embedding process: {e}\n{traceback.format_exc()}")
            raise

    def store_entry(self, records):
        """
        Stores embedding results in the database.

        :param records: List of embedding result dictionaries.
        :type records: list[dict]
        :raises RuntimeError: If an error occurs during database storage.

        Example:
            >>> records = [
            >>>     {"sequence_id": 1, "embedding_type_id": 2, "embedding": [0.1, 0.2], "shape": [2]}
            >>> ]
            >>> manager.store_entry(records)
        """
        session = self.session
        try:
            for record in records:
                embedding_entry = SequenceEmbedding(
                    sequence_id=record['sequence_id'],
                    embedding_type_id=record['embedding_type_id'],
                    embedding=record['embedding'],
                    shape=record['shape'],
                )
                session.add(embedding_entry)
            session.commit()

        except Exception as e:
            session.rollback()
            self.logger.error(f"Error during database storage: {e}")
            raise RuntimeError(f"Error storing entry: {e}")
