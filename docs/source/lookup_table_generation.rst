Lookup Table Generation for FANTASIA
====================================

This document describes the available pipelines to populate the functional lookup table used in FANTASIA. Two complementary pathways are supported:

1. **UniProt-based standard import**, which pulls accessions and metadata using APIs or curated CSVs.
2. **Custom annotation ingestion**, useful for local or third-party datasets with manually curated annotations and FASTA sequences.

Standard Accession Import
-------------------------

The `AccessionManager` class provides two standard methods to initialize protein accessions:

.. code-block:: python

    AccessionManager(conf).fetch_accessions_from_api()

Fetches accessions directly from UniProtKB using the `search_criteria` defined in the YAML configuration. The query must be a valid UniProt search string. For example:

.. code-block:: yaml

    search_criteria: '(structure_3d:true)'

To restrict results to experimentally validated proteins with GO annotations, use:

.. code-block:: yaml

    search_criteria: '(go_exp:* OR go_ida:* OR go_ipi:* OR go_imp:* OR go_igi:* OR go_iep:* OR go_tas:* OR go_ic:*)'

Alternatively, accessions can be loaded from a user-provided CSV:

.. code-block:: python

    AccessionManager(conf).load_accessions_from_csv()

This requires the configuration to define the file path and the relevant column:

.. code-block:: yaml

    load_accesion_csv: ../data/sample.csv
    load_accesion_column: uniprot_id

This mode is recommended for predefined accession lists or curated datasets.

Post-processing Steps
---------------------

Once accessions are available, metadata and protein representations are generated using:

.. code-block:: python

    UniProtExtractor(conf).start()
    SequenceEmbeddingManager(conf).start()

These modules:

- Download protein sequence and metadata from UniProt.
- Generate embeddings using selected protein language models.

Embedding Model Selection
-------------------------

Available embedding models are defined under `embedding.types` in the YAML configuration:

.. code-block:: yaml

    embedding:
      types:
        - 1  # ESM: Evolutionary Scale Modeling (Meta AI)
        - 2  # ProSTT5: Structural Transformer T5-based (Ana Rojas Lab)
        - 3  # ProtT5: Protein Transformer T5-based (EMBL/UniProt)
        - 4  # Ankh3: Contextual residue embedding model (Ankh v3)

Multiple models may be activated simultaneously. Batch sizes for queueing and inference are controlled via:

.. code-block:: yaml

    batch_size: 1
    batch_size_embedding: 1

Annotation Filtering by Evidence
--------------------------------

FANTASIA supports filtering GO annotations based on UniProt evidence codes. To retain only experimentally supported annotations:

.. code-block:: yaml

    allowed_evidences:
      - EXP  # Inferred from Experiment
      - IDA  # Inferred from Direct Assay
      - IPI  # Inferred from Physical Interaction
      - IMP  # Inferred from Mutant Phenotype
      - IGI  # Inferred from Genetic Interaction
      - IEP  # Inferred from Expression Pattern
      - TAS  # Traceable Author Statement
      - IC   # Inferred by Curator

If the list is left empty (`[]`), all annotations will be imported regardless of quality.

Custom Annotation via GOAnnotationsQueueProcessor
--------------------------------------------------

FANTASIA also supports local datasets or third-party annotations via the `GOAnnotationsQueueProcessor` class.

Requirements:

- A tab-separated annotation file (`goa_annotations_file`) with format:

  .. code-block::

      PROT_ID_001    GO:0008150,GO:0003674,GO:0005575

Execution:

.. code-block:: python

    GOAnnotationsQueueProcessor(conf).start()

This module performs the following steps internally:

1. Parses each protein entry and its GO terms.
2. Retrieves the protein sequence from UniProt.
3. Stores or updates the protein, sequence, GO terms, and assigns a default evidence code (`"UNKNOWN"`).

Configuration Summary
----------------------

Depending on the selected mode, the YAML configuration must include the appropriate keys. Only one mode should be active per execution.

.. code-block:: yaml

    # --- Mode 1: Standard UniProt Search (API query) ---
    # Triggered by: AccessionManager(conf).fetch_accessions_from_api()
    search_criteria: '(go_exp:* OR go_ida:* OR go_ipi:* OR go_imp:*)'
    tag: HUMAN_SEARCH
    allowed_evidences:
      - EXP
      - IDA
      - IPI
      - IMP
    embedding:
      types: [3, 4]     # e.g. ProtT5, Ankh3
      batch_size: 1

    # --- Mode 2: CSV-based Custom Dataset ---
    # Triggered by: AccessionManager(conf).load_accessions_from_csv()
    load_accesion_csv: ../data/sample.csv
    load_accesion_column: uniprot_id
    fasta_path: ../data/sequences.fasta
    tag: CUSTOM_DATASET
    allowed_evidences: [EXP, IDA, IPI, IMP]
    embedding:
      types: [3, 4]
      batch_size: 1

    # --- Mode 3: GOA File with Local Annotations ---
    # Triggered by: GOAnnotationsQueueProcessor(conf).start()
    goa_annotations_file: ../data/custom_go_annotations.tsv
    limit_execution: 1000  # Optional

Execution Flow
^^^^^^^^^^^^^^

The following illustrates the high-level execution logic, depending on the selected mode:

.. code-block:: python

    # --- Mode 1 ---
    AccessionManager(conf).fetch_accessions_from_api()
    UniProtExtractor(conf).start()

    # --- Mode 2 ---
    AccessionManager(conf).load_accessions_from_csv()
    UniProtExtractor(conf).start()

    # --- Mode 3 ---
    GOAnnotationsQueueProcessor(conf).start()

    # Common to all modes
    SequenceEmbeddingManager(conf).start()

Each configuration block must be properly defined in your YAML file. Do not mix multiple modes in a single execution context.
