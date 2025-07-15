import os
import traceback
import warnings

import gemmi
from Bio import PDB
from Bio.PDB import Select

from sqlalchemy.sql import or_

from protein_information_system.helpers.parser.parser import get_chain_to_accession_map
from protein_information_system.sql.model.entities.protein.accesion import Accession
from protein_information_system.sql.model.entities.sequence.sequence import Sequence
from protein_information_system.tasks.queue import QueueTaskInitializer
from protein_information_system.sql.model.entities.structure.structure import Structure
from protein_information_system.sql.model.entities.structure.chain import Chain
from protein_information_system.sql.model.entities.structure.state import State

warnings.filterwarnings("ignore")


class ChainSelect(Select):
    """
    A helper class used during PDB file parsing to filter chains and models.
    """

    def __init__(self, chain_id, model_id):
        self.chain_id = chain_id
        self.model_id = model_id

    def accept_chain(self, chain):
        """
        Filter to accept only the specified chain.
        """
        return chain.get_id() == self.chain_id

    def accept_model(self, model):
        """
        Filter to accept only the specified model.
        """
        return model.get_id() == self.model_id


class PDBExtractor(QueueTaskInitializer):
    """
    The PDBExtractor class manages the extraction and processing of protein structures
    from the Protein Data Bank (PDB). It handles the entire workflow from downloading
    PDB files, processing each model and chain, to storing the processed data in the
    system’s database.

    Attributes:
        reference_attribute (str): The attribute used to reference PDB entries, typically 'id'.
        data_directory (str): The root directory where data files are stored. Defaults to '/data'.
        pdb_directory (str): Directory where raw PDB files are stored.
        models_directory (str): Directory where processed model files are saved.
    """

    def __init__(self, conf):
        """
        Initialize the PDBExtractor.

        This constructor sets up the necessary directories for storing PDB files and
        processed models and initializes the configuration.

        Args:
            conf (dict): Configuration dictionary containing settings like data directories.
        """
        super().__init__(conf)
        self.reference_attribute = 'id'  # Actualmente 'id' en la clase Structure
        self.data_directory = os.path.expanduser(self.conf.get("data_directory", "/data"))
        self.pdb_directory = os.path.join(self.data_directory, 'pdb')
        self.models_directory = os.path.join(self.data_directory, 'models')
        self.setup_directories()

    def setup_directories(self):
        """
        Set up the directories needed for PDB file storage and processing.

        Creates the directories for storing raw PDB files and processed model files
        if they do not already exist.
        """
        for path in [self.pdb_directory, self.models_directory]:
            try:
                if not os.path.exists(path):
                    os.makedirs(path)
                    self.logger.info(f"Created directory: {path}")
                else:
                    self.logger.info(f"Directory already exists: {path}")
            except OSError as e:
                self.logger.error(f"Failed to create directory {path}: {e}")
                raise

    def enqueue(self):
        """
        Enqueue tasks for processing based on Structure entries.

        This method queries the database for Structure entries that meet certain criteria,
        such as resolution threshold or method type, and enqueues them for processing.
        """
        resolution_threshold = self.conf.get("resolution_threshold")
        limit_execution = self.conf.get("limit_execution")  # Nuevo parámetro de configuración

        if resolution_threshold is None:
            self.logger.error("Resolution threshold not defined in configuration.")
            return

        try:
            query = self.session.query(Structure).filter(
                or_(
                    Structure.resolution <= resolution_threshold,
                    Structure.method == "NMR"
                )
            )

            if limit_execution:  # Aplicar límite si está configurado
                structures = query.limit(limit_execution).all()
            else:
                structures = query.all()

            for structure in structures:
                self.logger.debug(f"Publishing task for PDB ID: {structure.id}")
                self.publish_task(structure.id)
        except Exception as e:
            self.logger.error(f"Error enqueuing tasks: {e}\n{traceback.format_exc()}")

    def process(self, pdb_id):
        """
        Process a PDB file, extracting models, chains, and associated UniProt accessions.

        Args:
            pdb_id (str): PDB ID to be processed.

        Returns:
            dict: Processed data, including chains and accessions.
        """
        pdb_list = PDB.PDBList(pdb=self.pdb_directory)
        data = {"pdb_id": pdb_id, "chains": []}
        amino_acids = {
            'ALA', 'ARG', 'ASN', 'ASP', 'CYS', 'GLU', 'GLY', 'HIS', 'ILE', 'LEU',
            'LYS', 'MET', 'PHE', 'PRO', 'SER', 'THR', 'TRP', 'TYR', 'VAL'
        }

        try:
            file_path = pdb_list.retrieve_pdb_file(
                pdb_id, file_format=self.conf.get("file_format", "mmCif"), pdir=self.pdb_directory
            )

            self.logger.info(f"Downloaded PDB {pdb_id} to {file_path}")

            # Get chain-to-accession mappings
            chain_to_accession_map = get_chain_to_accession_map(file_path)

            structure = gemmi.read_structure(file_path)
            structure.remove_ligands_and_waters()
            structure.remove_hydrogens()
            structure.remove_empty_chains()

            for model in structure:
                for chain in model:
                    clean_chain = gemmi.Chain(chain.name)
                    for residue in chain:
                        if residue.name in amino_acids:
                            clean_chain.add_residue(residue)

                    if not clean_chain:
                        continue

                    sequence = ''.join(gemmi.find_tabulated_residue(res.name).one_letter_code for res in clean_chain)
                    print(model.num)
                    clean_file_path = os.path.join(self.models_directory, f"{pdb_id}_{chain.name}_{model.num}.cif")

                    # Guardar la estructura limpia
                    clean_structure = gemmi.Structure()
                    clean_model = gemmi.Model(model.num)
                    clean_model.add_chain(clean_chain)
                    clean_structure.add_model(clean_model)
                    clean_structure.make_mmcif_document().write_file(clean_file_path)

                    accession = chain_to_accession_map.get(chain.name)

                    if not accession:
                        self.retrieve_accession_for_chain(pdb_id, chain.name)

                    data['chains'].append({
                        "chain_id": chain.name,
                        "sequence": sequence,
                        "file_path": clean_file_path,
                        "model": str(model.num),
                        "accession": chain_to_accession_map.get(chain.name)
                    })

                    self.logger.info(f"Saved cleaned chain {chain.name} of model {model.num} to {clean_file_path}")

        except Exception as e:
            self.logger.error(f"Error processing PDB {pdb_id}: {e}\n{traceback.format_exc()}")
            return {"error": str(e), "traceback": traceback.format_exc()}

        return data

    def store_entry(self, record):
        """
        Store the processed PDB data into the database.

        Args:
            record (dict): Processed data record to be stored.
        """
        pdb_id = record["pdb_id"]
        chains_data = record["chains"]

        try:
            # Recuperar la estructura asociada al PDB ID
            structure = self.session.query(Structure).filter_by(id=pdb_id).one()

            for chain_data in chains_data:
                # Crear o recuperar la secuencia
                sequence_entry = (
                    self.session.query(Sequence)
                    .filter_by(sequence=chain_data["sequence"])
                    .one_or_none()
                )
                if not sequence_entry:
                    sequence_entry = Sequence(sequence=chain_data["sequence"])
                    self.session.add(sequence_entry)
                    self.session.flush()
                    self.logger.debug(f"Created new sequence for chain {chain_data['chain_id']}.")

                # Crear o recuperar el accession si existe
                accession_entry = None
                if chain_data.get("accession"):
                    accession_entry = (
                        self.session.query(Accession)
                        .filter_by(code=chain_data["accession"])
                        .one_or_none()
                    )
                    if not accession_entry:
                        accession_entry = Accession(code=chain_data["accession"])
                        self.session.add(accession_entry)
                        self.session.flush()
                        self.logger.debug(f"Created new accession with code {chain_data['accession']}.")

                # Crear o recuperar la cadena
                chain = (
                    self.session.query(Chain)
                    .filter_by(name=chain_data["chain_id"], structure_id=structure.id)
                    .one_or_none()
                )
                if not chain:
                    chain = Chain(
                        name=chain_data["chain_id"],
                        structure_id=structure.id,
                        sequence_id=sequence_entry.id,
                        accession_code=accession_entry.code if accession_entry else None
                    )
                    self.session.add(chain)
                    self.logger.debug(f"Created new chain {chain_data['chain_id']} for structure {structure.id}.")

                # Crear o recuperar el estado
                state = (
                    self.session.query(State)
                    .filter_by(chain_id=chain.id, structure_id=structure.id, model_id=chain_data['model'])
                    .one_or_none()
                )
                if not state:
                    state = State(
                        model_id=chain_data['model'],
                        file_path=chain_data["file_path"],
                        chain_id=chain.id,
                        structure_id=structure.id
                    )
                    self.session.add(state)
                    self.logger.debug(f"Created new state for chain {chain.name} in structure {structure.id}.")

            # Confirmar los cambios
            self.session.commit()
            self.logger.info(f"Stored data for PDB ID: {pdb_id}")

        except Exception as e:
            self.session.rollback()
            self.logger.error(f"Failed to store data for PDB ID: {pdb_id}: {e}")
            raise

    def retrieve_accession_for_chain(self, pdb_id, chain_name):
        """
        Retrieves the accession for a specific chain in a PDB entry using a GraphQL query.

        Args:
            pdb_id (str): The ID of the PDB entry.
            chain_name (str): The chain name for which to retrieve the accession.

        Returns:
            str: The retrieved accession code or None if not found.
        """
        import requests

        # Define the GraphQL query
        graphql_query = """
        query structure($id: String!) {
          entry(entry_id: $id) {
            polymer_entities {
              polymer_entity_instances {
                rcsb_polymer_entity_instance_container_identifiers {
                  auth_asym_id
                }
              }
              rcsb_polymer_entity_container_identifiers {
                uniprot_ids
              }
            }
          }
        }
        """

        # Prepare the variables
        variables = {"id": pdb_id}

        # Correct endpoint for GraphQL
        graphql_endpoint = "https://data.rcsb.org/graphql"

        try:
            # Execute the query
            response = requests.post(
                graphql_endpoint,
                json={"query": graphql_query, "variables": variables},
                timeout=10
            )

            # Check for a successful response
            response.raise_for_status()

            # Parse the response JSON
            data = response.json()
            entities = data.get("data", {}).get("entry", {}).get("polymer_entities", [])

            # Search for the accession in the polymer entities
            for entity in entities:
                instances = entity.get("polymer_entity_instances", [])
                for instance in instances:
                    auth_asym_id = instance.get("rcsb_polymer_entity_instance_container_identifiers", {}).get(
                        "auth_asym_id")
                    if auth_asym_id == chain_name:
                        uniprot_ids = entity.get("rcsb_polymer_entity_container_identifiers", {}).get("uniprot_ids", [])
                        if uniprot_ids:
                            return uniprot_ids[0]  # Return the first UniProt ID found

            self.logger.info(f"No accession found for chain {chain_name} in PDB ID {pdb_id}.")
            return None

        except requests.exceptions.RequestException as e:
            self.logger.error(f"Failed to retrieve accession for chain {chain_name} in PDB ID {pdb_id}: {e}")
            return None
