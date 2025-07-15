import os
import unittest

import pytest

from protein_information_system.helpers.config.yaml import read_yaml_config
from protein_information_system.operation.extraction.pdb import PDBExtractor
from protein_information_system.sql.model.entities.protein.accesion import Accession
from protein_information_system.sql.model.entities.protein.protein import Protein
from protein_information_system.sql.model.entities.sequence.sequence import Sequence
from protein_information_system.sql.model.entities.structure.structure import Structure
from protein_information_system.sql.model.entities.structure.chain import Chain
from protein_information_system.sql.model.entities.structure.state import State

@pytest.mark.order(3)
class TestPDBExtractor(unittest.TestCase):

    def setUp(self):
        config_path = os.path.join('protein_information_system/config/', "config.yaml")
        config = read_yaml_config(config_path)
        constants_path = os.path.join('protein_information_system/config/', "constants.yaml")
        config['constants'] = constants_path

        config['limit_execution'] = 3

        self.extractor = PDBExtractor(config)


    def test_entities_created(self):
        """Verifica que se crean instancias en todas las entidades tras ejecutar el extractor."""
        # Ejecutar el extractor
        self.extractor.start()

        # Verificar Structures
        structures = self.extractor.session.query(Structure).all()
        self.assertGreater(len(structures), 0, "No se crearon estructuras en la base de datos.")

        # Verificar Chains
        chains = self.extractor.session.query(Chain).all()
        self.assertGreater(len(chains), 0, "No se crearon cadenas en la base de datos.")

        # Verificar States
        states = self.extractor.session.query(State).all()
        self.assertGreater(len(states), 0, "No se crearon estados en la base de datos.")

        # Verificar Sequences
        sequences = self.extractor.session.query(Sequence).all()
        self.assertGreater(len(sequences), 0, "No se crearon secuencias en la base de datos.")

        # Verificar Proteínas
        proteins = self.extractor.session.query(Protein).all()
        self.assertGreater(len(proteins), 0, "No se crearon proteínas en la base de datos.")

        # Verificar Accessions
        accessions = self.extractor.session.query(Accession).all()
        self.assertGreater(len(accessions), 0, "No se crearon accesiones en la base de datos.")

    if __name__ == '__main__':
        unittest.main()
