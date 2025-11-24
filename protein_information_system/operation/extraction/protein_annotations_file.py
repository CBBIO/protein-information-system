"""GO Annotations Queue Processor

This module defines :class:`GOAnnotationsQueueProcessor`, a queue-integrated
component of the Protein Information System (PIS) that:

- Parses CAFA-formatted GO annotation files (TSV).
- Loads and indexes protein sequences from a FASTA file.
- Publishes per-protein tasks to the internal queue.
- Persists proteins, accessions, sequences, GO terms (with category),
  and protein–GO associations into the relational database.

The implementation relies on ORM entities (Protein, Accession, Sequence,
GOTerm, ProteinGOTermAnnotation) and a task-queue base class
(:class:`QueueTaskInitializer`).

Notes
-----
- CAFA TSV format expected per line: ``UniProtKB:ID<TAB>GO:XXXXXXX<TAB>Category``.
  The category is optional in the file, but the DB model requires a category
  to create a new GO term; missing categories are therefore skipped.
- FASTA headers are expected to follow UniProt convention like
  ``sp|P12345|...`` or ``tr|Q8XYZ1|...``; accession is extracted from the
  second field split by ``'|'``. If not present, the full record ID is used.
- Evidence codes for associations default to ``"UNKNOWN"`` unless provided by
  upstream sources.
"""
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from Bio import SeqIO

from protein_information_system.sql.model.entities.go_annotation.go_annotation import (
    ProteinGOTermAnnotation,
)
from protein_information_system.sql.model.entities.go_annotation.go_term import GOTerm
from protein_information_system.sql.model.entities.protein.accesion import Accession
from protein_information_system.sql.model.entities.protein.protein import Protein
from protein_information_system.sql.model.entities.sequence.sequence import Sequence
from protein_information_system.tasks.queue import QueueTaskInitializer


class GOAnnotationsQueueProcessor(QueueTaskInitializer):
    """Queue processor for Gene Ontology annotations.

    Parameters
    ----------
    conf : dict
        Configuration mapping. Requires the following keys:
        - ``goa_annotations_file``: Path to the CAFA-formatted TSV file.
        - ``goa_sequences_fasta``: Path to the FASTA file with protein sequences.
        - ``limit_execution`` (optional): Integer limit for number of TSV lines to process.

    Side Effects
    ------------
    - Loads all sequences from FASTA into an in-memory dictionary at initialization
      (``self.sequences``) to enable fast lookups during processing.
    """

    def __init__(self, conf: dict) -> None:
        super().__init__(conf)
        self.file_path: str = self.conf["goa_annotations_file"]
        self.fasta_path: str = self.conf["goa_sequences_fasta"]
        self.sequences: Dict[str, str] = self.load_sequences()

    def load_sequences(self) -> Dict[str, str]:
        """Load sequences from the configured FASTA file into memory.

        FASTA records are indexed by UniProt accession (preferred) or by the
        raw record ID when an accession cannot be parsed.

        Returns
        -------
        dict
            Mapping ``{uniprot_accession: amino_acid_sequence}``.
        """
        seq_dict: Dict[str, str] = {}
        try:
            for record in SeqIO.parse(self.fasta_path, "fasta"):
                # Attempt to extract UniProt accession from a header like
                #   sp|P12345|ENTRY_NAME ...
                parts = record.id.split("|")
                if len(parts) >= 3:
                    uniprot_id = parts[1]  # e.g., P12345
                else:
                    uniprot_id = record.id
                seq_dict[uniprot_id] = str(record.seq)
            self.logger.info(f"Loaded {len(seq_dict)} sequences from FASTA.")
        except Exception as e:
            # Surface FASTA parsing/IO errors with context; re-raise for caller handling.
            self.logger.error(f"Error loading FASTA: {e}")
            raise
        return seq_dict

    def enqueue(self) -> None:
        """Enqueue per-protein tasks parsed from a CAFA-formatted TSV file.

        Expected TSV columns per line::

            UniProtKB:ID<TAB>GO:XXXXXXX<TAB>Category

        - Lines beginning with ``#`` or blank lines are ignored.
        - The third column (``Category``) is optional in the file; if absent or blank,
          ``"UNKNOWN"`` is assigned. Unknown categories are allowed at enqueue time
          but may later cause GO term creation to be skipped during storage.
        - Entries are grouped by protein accession before publishing to the queue.
        """
        self.logger.info(f"Enqueueing tasks from CAFA file: {self.file_path}")
        try:
            with open(self.file_path, "r") as f:
                lines = f.readlines()

            limit_execution = self.conf.get("limit_execution")
            if limit_execution and isinstance(limit_execution, int):
                lines = lines[:limit_execution]
                self.logger.info(
                    f"Limiting to the first {limit_execution} entries."
                )

            annotations: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
            for raw in lines:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue

                parts = line.split("\t")
                if len(parts) < 2:
                    # Malformed; require at least protein and GO ID
                    continue

                protein_id_raw = parts[0]
                go_id = parts[1]
                category = (
                    parts[2].strip() if len(parts) >= 3 and parts[2].strip() else "UNKNOWN"
                )

                # Normalize UniProtKB prefix to bare accession (e.g., "UniProtKB:P12345" -> "P12345")
                protein_id = protein_id_raw.replace("UniProtKB:", "")
                annotations[protein_id].append((go_id, category))

            # Publish one task per protein with de-duplicated (go_id, category) pairs
            for protein_entry_id, go_items in annotations.items():
                task_data = {
                    "protein_entry_id": protein_entry_id,
                    "go_terms": list(set(go_items)),  # list[tuple(go_id, category)]
                }
                self.publish_task(task_data)

        except Exception as e:
            self.logger.error(f"Error enqueuing tasks: {e}")
            raise

    def process(self, data: dict) -> dict:
        """Resolve sequence and return a normalized task result.

        Parameters
        ----------
        data : dict
            Task payload with keys:
            - ``protein_entry_id`` (str): Protein accession.
            - ``go_terms`` (list[tuple[str, str]]): GO ID and category pairs.

        Returns
        -------
        dict
            Result payload with keys: ``protein``, ``go_terms``, ``sequence``.
        """
        protein_entry_id: str = data["protein_entry_id"]
        go_terms: List[Tuple[str, str]] = data["go_terms"]
        sequence: Optional[str] = self.get_sequence_from_external_source(protein_entry_id)

        result = {
            "protein": protein_entry_id,
            "go_terms": go_terms,
            "sequence": sequence,
        }
        return result

    def get_sequence_from_external_source(self, protein_entry_id: str) -> Optional[str]:
        """Retrieve the sequence for a protein from the in-memory FASTA index.

        Parameters
        ----------
        protein_entry_id : str
            UniProt accession for which to retrieve the sequence.

        Returns
        -------
        Optional[str]
            The amino acid sequence if found; otherwise ``None``.
        """
        sequence = self.sequences.get(protein_entry_id)
        if not sequence:
            self.logger.warning(
                f"Sequence not found in FASTA for {protein_entry_id}"
            )
        return sequence

    def store_entry(self, data: dict) -> None:
        """Persist a processed protein entry into the database.

        This method performs the following steps in a transactional manner:

        1. Ensure the existence of the Protein and its corresponding Accession record.
        2. Link the Protein to a Sequence if available, creating the sequence record
           on demand.
        3. Ensure the existence of all referenced GO terms (one by one).
        4. Collect the set of GO associations (protein_id, go_id) to be created.
        5. Query in bulk which associations already exist for this protein.
        6. Insert only the missing associations in a single bulk statement
           (multi-values INSERT).
        7. Commit all changes once at the end.

        Compared to the previous row-by-row approach, this implementation
        eliminates the N+1 query pattern and reduces overhead by performing
        association inserts in bulk. This significantly improves throughput when
        handling proteins with a large number of GO annotations.

        Parameters
        ----------
        data : dict
            Parsed entry with at least:
              - "protein": str, protein identifier
              - "sequence": Optional[str], raw protein sequence
              - "go_terms": List[Tuple[str, str]], list of (go_id, category)
        """
        from sqlalchemy import select, insert

        try:
            # 1) Upsert Protein and Accession (reuse existing helpers)
            protein = self.get_or_create_protein(data["protein"])
            self.get_or_create_accession(
                code=data["protein"], protein_id=protein.id, primary=True
            )

            # 2) Upsert Sequence if provided
            if not data.get("sequence"):
                self.logger.warning(
                    f"No sequence for {protein.id}; skipping protein.sequence link."
                )
            else:
                sequence = self.get_or_create_sequence(data["sequence"])
                self.session.flush()  # ensure sequence.id is available
                protein.sequence_id = sequence.id

            # 3) Ensure GO terms and collect their IDs
            incoming_pairs = list(set(data["go_terms"]))  # de-duplicate in memory
            go_ids_to_link = []

            for go_id, category in incoming_pairs:
                if not category or str(category).strip() == "":
                    # Only reuse existing GO term if category is missing/invalid
                    self.logger.warning(
                        f"GO {go_id} with empty/invalid category; attempting reuse only."
                    )
                    go_term_entry = (
                        self.session.query(GOTerm).filter_by(go_id=go_id).first()
                    )
                    if not go_term_entry:
                        continue
                else:
                    # Upsert GO term with provided category
                    go_term_entry = self.get_or_create_go_term(go_id, category)

                go_ids_to_link.append(go_term_entry.go_id)

            if not go_ids_to_link:
                # Nothing to associate; still commit possible Accession/Sequence updates
                self.session.commit()
                self.logger.info(
                    f"Protein {protein.id} updated (no GO associations to add)."
                )
                return

            # 4) Query existing associations in bulk for this protein
            go_ids_to_link = list(set(go_ids_to_link))  # ensure uniqueness
            existing_go_ids = set(
                self.session.execute(
                    select(ProteinGOTermAnnotation.go_id).where(
                        ProteinGOTermAnnotation.protein_id == protein.id,
                        ProteinGOTermAnnotation.go_id.in_(go_ids_to_link),
                    )
                ).scalars()
            )

            # 5) Build the payload for missing associations only
            missing = [
                {
                    "protein_id": protein.id,
                    "go_id": go_id,
                    "evidence_code": "UNKNOWN",  # default evidence code
                }
                for go_id in go_ids_to_link
                if go_id not in existing_go_ids
            ]

            # 6) Bulk insert missing associations in a single statement
            if missing:
                self.session.execute(
                    insert(ProteinGOTermAnnotation.__table__),  # Core multi-values
                    missing,
                )

            # 7) Final commit (even if helpers may have committed earlier)
            self.session.commit()
            self.logger.info(
                f"Protein {protein.id} successfully updated: "
                f"{len(go_ids_to_link)} GO(s) seen, {len(missing)} new association(s) inserted."
            )

        except Exception as e:
            self.session.rollback()
            self.logger.error(
                f"Failed to store data for protein entry {data['protein']}: {e}"
            )
            raise

    def get_or_create_sequence(self, sequence: str) -> Sequence:
        """Create or retrieve a :class:`Sequence` entity by raw sequence value.

        Parameters
        ----------
        sequence : str
            Amino acid sequence string.

        Returns
        -------
        Sequence
            ORM instance corresponding to the stored sequence.

        Raises
        ------
        ValueError
            If ``sequence`` is empty or ``None``.
        """
        if not sequence:
            raise ValueError("No sequence available (None/empty).")
        existing_sequence: Optional[Sequence] = (
            self.session.query(Sequence).filter_by(sequence=sequence).first()
        )
        if not existing_sequence:
            existing_sequence = Sequence(sequence=sequence)
            self.session.add(existing_sequence)
            self.logger.debug(
                f"Created new sequence record for sequence {sequence[:10]}..."
            )
        else:
            self.logger.debug(
                f"Found existing sequence record for sequence {sequence[:10]}..."
            )
        return existing_sequence

    def get_or_create_association(
        self, protein_id: str, go_id: str, evidence_code: str = "UNKNOWN"
    ) -> Optional[ProteinGOTermAnnotation]:
        """Create or retrieve a protein–GO association.

        Parameters
        ----------
        protein_id : str
            Protein identifier (UniProt accession).
        go_id : str
            GO term identifier (e.g., ``GO:0008150``).
        evidence_code : str, optional
            Evidence code for the association (default ``"UNKNOWN"``).

        Returns
        -------
        Optional[ProteinGOTermAnnotation]
            Existing ORM association if found, otherwise ``None`` (a new one is queued
            in the session but not yet flushed when created).
        """
        try:
            existing = (
                self.session.query(ProteinGOTermAnnotation)
                .filter_by(protein_id=protein_id, go_id=go_id)
                .first()
            )
            if not existing:
                assoc = ProteinGOTermAnnotation(
                    protein_id=protein_id, go_id=go_id, evidence_code=evidence_code
                )
                self.session.add(assoc)
                self.logger.debug(
                    "Created new association for protein %s and GO term %s with evidence code %s.",
                    protein_id,
                    go_id,
                    evidence_code,
                )
            else:
                self.logger.debug(
                    "Association already exists for protein %s and GO term %s.",
                    protein_id,
                    go_id,
                )
            return existing
        except Exception as e:
            self.logger.error(
                f"Error retrieving or creating GO term association: {e}"
            )
            raise

    def get_or_create_protein(self, protein_entry_id: str) -> Protein:
        """Create or retrieve a :class:`Protein` by UniProt accession.

        Parameters
        ----------
        protein_entry_id : str
            UniProt accession used as the primary key in the ``Protein`` table.

        Returns
        -------
        Protein
            ORM instance for the protein.
        """
        protein: Optional[Protein] = (
            self.session.query(Protein).filter_by(id=protein_entry_id).first()
        )
        if not protein:
            protein = Protein(id=protein_entry_id)
            self.session.add(protein)
            self.session.commit()
            self.logger.debug(
                f"Created new protein record for protein entry ID {protein_entry_id}."
            )
        else:
            self.logger.debug(
                f"Protein record found for protein entry ID {protein_entry_id}."
            )
        return protein

    def get_or_create_go_term(self, go_id: str, category: str) -> GOTerm:
        """Create or update a :class:`GOTerm` with its category.

        Parameters
        ----------
        go_id : str
            GO term identifier (e.g., ``GO:0008150``).
        category : str
            GO category label (``BP``, ``MF``, or ``CC``). The DB schema enforces
            non-null constraints; empty/``None`` categories are rejected.

        Returns
        -------
        GOTerm
            ORM instance corresponding to the GO term.

        Raises
        ------
        ValueError
            If ``category`` is empty or ``None``.
        """
        category = None if category is None else str(category).strip()
        if not category:
            raise ValueError(
                f"Category is required to create GO term {go_id} (go_terms.category is NOT NULL)."
            )

        go_term_entry: Optional[GOTerm] = (
            self.session.query(GOTerm).filter_by(go_id=go_id).first()
        )
        if not go_term_entry:
            go_term_entry = GOTerm(go_id=go_id, category=category)
            self.session.add(go_term_entry)
            self.session.commit()
            self.logger.debug(
                f"Created new GO term entry for GO term {go_id} with category '{category}'."
            )
        else:
            # If an existing category differs from the incoming one, update policy is to override
            if getattr(go_term_entry, "category", None) != category:
                self.logger.info(
                    "GO term %s exists with category '%s', incoming '%s'. Updating to incoming.",
                    go_id,
                    getattr(go_term_entry, "category", None),
                    category,
                )
                go_term_entry.category = category
                self.session.commit()
        return go_term_entry

    def get_or_create_accession(
        self, code: str, protein_id: str, primary: bool = True, tag: Optional[str] = None
    ) -> Accession:
        """Create or retrieve a UniProt :class:`Accession` linked to a protein.

        Parameters
        ----------
        code : str
            Accession code (e.g., ``P12345``) used as the primary key in ``Accession``.
        protein_id : str
            Identifier of the linked :class:`Protein` (should match UniProt accession).
        primary : bool, optional
            Whether this accession is the primary one for the protein (default ``True``).
        tag : Optional[str], optional
            Optional qualifier/tag for the accession.

        Returns
        -------
        Accession
            ORM instance corresponding to the accession.
        """
        accession: Optional[Accession] = self.session.query(Accession).filter_by(code=code).first()
        if not accession:
            accession = Accession(
                code=code, primary=primary, tag=tag, protein_id=protein_id
            )
            self.session.add(accession)
            self.logger.debug(
                f"Created new accession '{code}' for protein {protein_id}."
            )
        else:
            # If the accession exists but points to a different protein, fix the linkage
            if accession.protein_id != protein_id:
                accession.protein_id = protein_id
                self.logger.debug(
                    f"Updated accession '{code}' to link to protein {protein_id}."
                )
        return accession
