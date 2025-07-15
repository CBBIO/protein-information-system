import os
import unittest

import pytest
from sqlalchemy.dialects.mssql.information_schema import sequences

from protein_information_system.helpers.config.yaml import read_yaml_config
from protein_information_system.operation.extraction.uniprot import UniProtExtractor
from protein_information_system.sql.model.entities.go_annotation.go_annotation import ProteinGOTermAnnotation
from protein_information_system.sql.model.entities.go_annotation.go_term import GOTerm
from protein_information_system.sql.model.entities.protein.accesion import Accession
from protein_information_system.sql.model.entities.protein.protein import Protein
from protein_information_system.sql.model.entities.sequence.sequence import Sequence
from protein_information_system.sql.model.entities.structure.structure import Structure

@pytest.mark.order(2)
class TestUniProtExtractor(unittest.TestCase):

    def setUp(self):
        config_path = os.path.join('protein_information_system/config/', "config.yaml")
        config = read_yaml_config(config_path)
        constants_path = os.path.join('protein_information_system/config/', "constants.yaml")
        config['constants'] = constants_path
        config['limit_execution'] = 5

        self.extractor = UniProtExtractor(config)

    def get_config_path(self):
        """Devuelve la ruta al archivo de configuración."""
        return os.path.join('tests/config/', "config.yaml")  # Ruta del archivo YAML de prueba

    def test_entities_created(self):
        """Verifica que se crean instancias en todas las entidades tras ejecutar el extractor."""
        # Ejecutar el extractor
        self.extractor.start()

        # Verificar GO Terms
        go_terms = self.extractor.session.query(GOTerm).all()
        self.assertGreater(len(go_terms), 0, "No se crearon términos GO en la base de datos.")

        # Verificar GO Annotations
        go_annotations = self.extractor.session.query(ProteinGOTermAnnotation).all()
        self.assertGreater(len(go_annotations), 0, "No se crearon anotaciones GO en la base de datos.")

        # Verificar Structures
        structures = self.extractor.session.query(Structure).all()
        self.assertGreater(len(structures), 0, "No se crearon estructuras en la base de datos.")

        # Verificar Sequences
        sequences = self.extractor.session.query(Sequence).all()
        self.assertGreater(len(sequences), 0, "No se crearon secuencias en la base de datos.")

        # Verificar Proteínas
        proteins = self.extractor.session.query(Protein).all()
        self.assertGreater(len(proteins), 0, "No se crearon proteínas en la base de datos.")

    if __name__ == '__main__':
        unittest.main()
