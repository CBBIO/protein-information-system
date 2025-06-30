Adding Embedding Models
========================

This document explains how to add a new sequence embedding model to the FANTASIA system. The system is modular and dynamically integrates new models by simply updating a constants file and implementing a compatible module.

Overview
--------

Sequence embedding models are declared in the `sequence_embedding_types` section of the `constants.yaml` file. The system automatically syncs this configuration with the database table `sequence_embedding_type`, so **you do not need to modify the database manually**.

At runtime, the `SequenceEmbeddingManager` dynamically loads all declared models using Python's `importlib` and the `task_name` defined for each embedding model.

Integration Steps
-----------------

1. Declare the Model in `constants.yaml`
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Edit the file `config/constants.yaml` and add a new entry under `sequence_embedding_types`:

.. code-block:: yaml

    sequence_embedding_types:
      - name: "MyModel"
        description: "Description of the new model"
        task_name: "mymodel"
        model_name: "my-huggingface-org/model"

- `name`: Friendly name used in logs or interfaces.
- `description`: Human-readable explanation.
- `task_name`: Must match the Python filename (without `.py`) that implements the logic.
- `model_name`: Passed to `transformers.from_pretrained(...)` or your custom loader.

2. Implement the Python Module
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Create a file in:

.. code-block::

    protein_information_system/operation/embedding/proccess/sequence/mymodel.py

This module must implement the following three functions:

.. code-block:: python

    def load_model(model_name, conf):
        # Load and return the model
        ...

    def load_tokenizer(model_name):
        # Load and return the tokenizer
        ...

    def embedding_task(batch, model, tokenizer, device, batch_size=8, embedding_type_id=None):
        # Apply model and return a list of dictionaries
        ...

Each item returned by `embedding_task()` must be a dictionary with the following keys:

.. code-block:: python

    {
        "sequence_id": <int>,
        "embedding_type_id": <int>,
        "sequence": <str>,
        "embedding": <np.ndarray>,
        "shape": <tuple>
    }

3. Add the Model ID to the Execution Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In your main config file (e.g., `config.yaml`), register the model ID under `embedding.types`. For example, if the new model was assigned ID 5:

.. code-block:: yaml

    embedding:
      types: [1, 2, 3, 4, 5]
      batch_size: 1
      batch_size_embedding: 1
      device: cuda
      use_fp16: true

4. Execute the Pipeline
^^^^^^^^^^^^^^^^^^^^^^^

Run your main pipeline (e.g., `main.py`):

.. code-block:: bash

    python main.py

This will trigger:

- Loading and checking services.
- Accession fetching (API or CSV).
- UniProt data extraction.
- **Dynamic embedding model loading**.
- Embedding generation and storage.
- Structure 3Di processing.

All models registered in the `constants.yaml` and activated via `embedding.types` will be applied to all sequences not yet embedded for that type.

Execution Context
-----------------

The logic is orchestrated from:

.. code-block:: python

    SequenceEmbeddingManager(conf).start()

This class loads all active `embedding_type_id`s and executes:

- `enqueue()` → batching and task creation
- `process()` → model inference
- `store_entry()` → database insertion

The embedding modules are dynamically imported from:

.. code-block:: python

    importlib.import_module("...embedding.proccess.sequence.<task_name>")

Summary Table
-------------

The following table summarizes the required elements to add a new embedding model:

.. list-table::
   :widths: 25 75
   :header-rows: 1

   * - Field / Method
     - Description
   * - ``name``
     - Display name for the model (for humans)
   * - ``task_name``
     - Name of the Python module to import dynamically
   * - ``model_name``
     - Model path or identifier (e.g. HuggingFace model name)
   * - ``load_model()``
     - Loads the actual model (Torch/Transformers)
   * - ``load_tokenizer()``
     - Loads tokenizer compatible with the model
   * - ``embedding_task()``
     - Performs inference and returns embedding records
   * - ``embedding.types``
     - List of model IDs to execute, defined in ``config.yaml``

Notes
-----

- The system ensures embeddings are only computed if they do not already exist for the target model.
- Models are isolated and modular: changes to one model do not affect others.
- For debugging, you can set `limit_execution` in the config to restrict the number of sequences.

