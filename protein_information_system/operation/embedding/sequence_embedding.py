import importlib
import traceback

from protein_information_system.sql.model.entities.embedding.sequence_embedding import (
    SequenceEmbeddingType,
    SequenceEmbedding,
)

from protein_information_system.sql.model.entities.sequence.sequence import Sequence
from protein_information_system.tasks.gpu import GPUTaskInitializer


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

                batch_size = model_conf[type_obj.name].get('batch_size', 1)

                # 🔧 NUEVO: normalizar layer_index (int -> [int], lista -> lista)
                raw_layer_index = model_conf[type_obj.name].get('layer_index', [0])
                if isinstance(raw_layer_index, int):
                    layer_index = [raw_layer_index]
                else:
                    layer_index = list(raw_layer_index) if raw_layer_index else [0]

                types[type_obj.name] = {
                    'name': type_obj.name,
                    'module': module,
                    'model_name': type_obj.model_name,
                    'id': type_obj.id,
                    'task_name': type_obj.task_name,
                    'batch_size': batch_size,
                    'layer_index': layer_index,  # ✅ ya definido
                }

        return types

    def enqueue(self):
        """
        Enqueue sequence-embedding tasks for all models, requesting only the *missing* layers.

        Behavior
        --------
        For each (sequence, embedding model type):
          1) Read the desired layer indices from configuration (e.g., [0, 1, 2]).
          2) Query the database for already-present layers for (sequence_id, embedding_type_id).
          3) Compute the set difference → 'missing_layers'.
          4) If any layers are missing, publish a single task payload for that sequence/model
             that includes *only* those missing layer indices.

        Batching
        --------
        Sequences are chunked into batches of size `self.queue_batch_size` to control memory and
        message size. For each batch, messages are grouped per model (backend) to minimize queue traffic.

        Notes
        -----
        - This function assumes the DB schema has a `layer_index` column on the `sequence_embeddings`
          table and that downstream storage (`store_entry`) includes this value when inserting.
        - It is recommended to add a UNIQUE constraint on
              (sequence_id, embedding_type_id, layer_index)
          to prevent duplicates in concurrent/parallel workers.

        Raises
        ------
        Exception
            Re-raises any unexpected error after logging.

        """
        try:
            self.logger.info("Starting embedding enqueue process.")
            self.session_init()
            sequences = self.session.query(Sequence).all()

            # Optional max-length filter (configured)
            max_length = self.conf['embedding'].get('max_sequence_length')
            if max_length:
                for s in sequences:
                    if s.sequence and len(s.sequence) > max_length:
                        s.sequence = s.sequence[:max_length]

            # Optional execution limit (for debugging or staged runs)
            if self.conf['limit_execution']:
                sequences = sequences[:self.conf['limit_execution']]

            # Chunk sequences to limit memory and size of per-publish batches
            sequence_batches = [
                sequences[i: i + self.queue_batch_size]
                for i in range(0, len(sequences), self.queue_batch_size)
            ]

            for batch in sequence_batches:
                # Group outgoing messages by model (backend)
                model_batches = {}

                for sequence in batch:
                    for model_name, type_info in self.types.items():
                        # Desired layers for this embedding type (e.g., [0, 1, 2])
                        desired_layers = type_info['layer_index']

                        # Fetch already stored layers for (sequence, type)
                        existing_layers = {
                            li for (li,) in
                            self.session.query(SequenceEmbedding.layer_index)
                            .filter_by(
                                sequence_id=sequence.id,
                                embedding_type_id=type_info['id']
                            )
                            .all()
                        }

                        # Compute missing layers in config order (stable iteration)
                        missing_layers = [li for li in desired_layers if li not in existing_layers]

                        if missing_layers:
                            task_data = {
                                'sequence': sequence.sequence,
                                'sequence_id': sequence.id,
                                'model_name': type_info['model_name'],
                                'embedding_type_id': type_info['id'],
                                # CRITICAL: pass only the missing layers for this sequence+model
                                'layer_index': missing_layers,
                            }
                            model_batches.setdefault(model_name, []).append(task_data)

                # Publish a single message per model grouping for this batch
                for model_name, batch_data in model_batches.items():
                    if batch_data:
                        self.publish_task(batch_data, model_name)
                        self.logger.info(
                            "Published batch with %d sequences to model '%s' (type ID %s).",
                            len(batch_data), model_name, self.types[model_name]['id']
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

            layer_index_list = self.types[model_type].get('layer_index', [0])

            embedding_records = module.embedding_task(
                sequences=batch_data,
                model=model,
                tokenizer=tokenizer,
                device=device,
                batch_size=batch_size,
                embedding_type_id=embedding_type_id,
                layer_index_list=layer_index_list,
            )
            return embedding_records

        except Exception as e:
            self.logger.error(f"Error during embedding process: {e}\n{traceback.format_exc()}")
            raise

    def store_entry(self, records):
        session = self.session
        try:
            if not records:
                return

            values = [
                dict(
                    sequence_id=r['sequence_id'],
                    embedding_type_id=r['embedding_type_id'],
                    layer_index=r['layer_index'],
                    embedding=r['embedding'],
                    shape=r['shape'],
                )
                for r in records
            ]
            from sqlalchemy.dialects.postgresql import insert  # <-- IMPORT NECESARIO

            stmt = insert(SequenceEmbedding).values(values)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=['sequence_id', 'embedding_type_id', 'layer_index']
            )

            session.execute(stmt)
            session.commit()

        except Exception as e:
            session.rollback()
            self.logger.error(f"Error during database storage: {e}")
            raise RuntimeError(f"Error storing entry: {e}")
