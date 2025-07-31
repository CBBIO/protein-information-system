from collections import defaultdict
from Bio import SeqIO

from protein_information_system.sql.model.entities.go_annotation.go_annotation import ProteinGOTermAnnotation
from protein_information_system.sql.model.entities.go_annotation.go_term import GOTerm
from protein_information_system.sql.model.entities.protein.protein import Protein
from protein_information_system.sql.model.entities.sequence.sequence import Sequence
from protein_information_system.tasks.queue import QueueTaskInitializer


class GOAnnotationsQueueProcessor(QueueTaskInitializer):
    def __init__(self, conf):
        super().__init__(conf)
        self.file_path = self.conf['goa_annotations_file']
        self.fasta_path = self.conf['goa_sequences_fasta']
        self.sequences = self.load_sequences()

    def load_sequences(self):
        """
        Load sequences from a local FASTA file into a dictionary.
        """
        seq_dict = {}
        try:
            for record in SeqIO.parse(self.fasta_path, "fasta"):
                # Remove prefix if any (e.g., >sp|P12345|...)
                uniprot_id = record.id.split('|')[-1] if '|' in record.id else record.id
                seq_dict[uniprot_id] = str(record.seq)
            self.logger.info(f"Loaded {len(seq_dict)} sequences from FASTA.")
        except Exception as e:
            self.logger.error(f"Error loading FASTA: {e}")
            raise
        return seq_dict

    def enqueue(self):
        """
        Enqueue tasks from CAFA-formatted TSV file: UniProtKB:ID<TAB>GO:XXXXXXX
        """
        self.logger.info(f"Enqueueing tasks from CAFA file: {self.file_path}")
        try:
            with open(self.file_path, 'r') as f:
                lines = f.readlines()

            limit_execution = self.conf.get("limit_execution")
            if limit_execution and isinstance(limit_execution, int):
                lines = lines[:limit_execution]
                self.logger.info(f"Limiting to the first {limit_execution} entries.")

            annotations = defaultdict(list)
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) != 2:
                    continue
                protein_id_raw, go_id = parts
                protein_id = protein_id_raw.replace("UniProtKB:", "")
                annotations[protein_id].append(go_id)

            for protein_entry_id, go_terms in annotations.items():
                task_data = {
                    'protein_entry_id': protein_entry_id,
                    'go_terms': list(set(go_terms))
                }
                self.publish_task(task_data)
                self.logger.info(f"Task enqueued for {protein_entry_id} with {len(go_terms)} GO terms.")

        except Exception as e:
            self.logger.error(f"Error enqueuing tasks: {e}")
            raise

    def process(self, data):
        """
        Get sequence from local FASTA and prepare task result.
        """
        protein_entry_id = data['protein_entry_id']
        go_terms = data['go_terms']
        sequence = self.get_sequence_from_external_source(protein_entry_id)

        result = {
            'protein': protein_entry_id,
            'go_terms': go_terms,
            'sequence': sequence
        }
        return result

    def get_sequence_from_external_source(self, protein_entry_id):
        """
        Retrieve the sequence from the preloaded FASTA file.
        """
        sequence = self.sequences.get(protein_entry_id)
        if not sequence:
            self.logger.warning(f"Sequence not found in FASTA for {protein_entry_id}")
        return sequence

    def store_entry(self, data):
        try:
            self.logger.debug(f"Storing data for protein entry {data['protein']}.")
            protein = self.get_or_create_protein(data['protein'])
            sequence = self.get_or_create_sequence(data['sequence'])

            if protein.sequence_id != sequence.id:
                protein.sequence_id = sequence.id
                self.logger.debug(f"Linked sequence to protein {protein.id}.")

            for go_term in data['go_terms']:
                go_term_entry = self.get_or_create_go_term(go_term)
                self.get_or_create_association(protein.id, go_term_entry.go_id)

            self.session.commit()
            self.logger.info(f"Protein {protein.id} successfully updated with sequence and GO terms.")

        except Exception as e:
            self.session.rollback()
            self.logger.error(f"Failed to store data for protein entry {data['protein']}: {e}")
            raise

    def get_or_create_sequence(self, sequence):
        try:
            existing_sequence = self.session.query(Sequence).filter_by(sequence=sequence).first()
            if not existing_sequence:
                existing_sequence = Sequence(sequence=sequence)
                self.session.add(existing_sequence)
                self.logger.debug(f"Created new sequence record for sequence {sequence[:10]}...")
            else:
                self.logger.debug(f"Found existing sequence record for sequence {sequence[:10]}...")
            return existing_sequence
        except Exception as e:
            self.logger.error(f"Error retrieving or creating sequence: {e}")
            raise

    def get_or_create_association(self, protein_id, go_id, evidence_code="UNKNOWN"):
        try:
            existing_association = self.session.query(ProteinGOTermAnnotation).filter_by(
                protein_id=protein_id, go_id=go_id).first()
            if not existing_association:
                association = ProteinGOTermAnnotation(
                    protein_id=protein_id, go_id=go_id, evidence_code=evidence_code)
                self.session.add(association)
                self.logger.debug(
                    f"Created new association for protein {protein_id} and GO term {go_id} with evidence code {evidence_code}.")
            else:
                self.logger.debug(f"Association already exists for protein {protein_id} and GO term {go_id}.")
            return existing_association
        except Exception as e:
            self.logger.error(f"Error retrieving or creating GO term association: {e}")
            raise

    def get_or_create_protein(self, protein_entry_id):
        protein = self.session.query(Protein).filter_by(id=protein_entry_id).first()
        if not protein:
            protein = Protein(id=protein_entry_id)
            self.session.add(protein)
            self.session.commit()
            self.logger.debug(f"Created new protein record for protein entry ID {protein_entry_id}.")
        else:
            self.logger.debug(f"Protein record found for protein entry ID {protein_entry_id}.")
        return protein

    def get_or_create_go_term(self, go_term):
        go_term_entry = self.session.query(GOTerm).filter_by(go_id=go_term).first()
        if not go_term_entry:
            go_term_entry = GOTerm(go_id=go_term)
            self.session.add(go_term_entry)
            self.session.commit()
            self.logger.debug(f"Created new GO term entry for GO term {go_term}.")
        return go_term_entry
