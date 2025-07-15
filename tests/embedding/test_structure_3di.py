import os
import unittest

import pytest
from sqlalchemy.dialects.mssql.information_schema import sequences

from protein_information_system.helpers.config.yaml import read_yaml_config
from protein_information_system.operation.embedding.sequence_embedding import SequenceEmbeddingManager
from protein_information_system.operation.embedding.structure_3di import Structure3DiManager
from protein_information_system.operation.extraction.uniprot import UniProtExtractor
from protein_information_system.sql.model.entities.embedding.sequence_embedding import SequenceEmbedding, \
    SequenceEmbeddingType
from protein_information_system.sql.model.entities.embedding.structure_3di import Structure3Di
from protein_information_system.sql.model.entities.go_annotation.go_annotation import ProteinGOTermAnnotation
from protein_information_system.sql.model.entities.go_annotation.go_term import GOTerm
from protein_information_system.sql.model.entities.protein.accesion import Accession
from protein_information_system.sql.model.entities.protein.protein import Protein
from protein_information_system.sql.model.entities.sequence.sequence import Sequence
from protein_information_system.sql.model.entities.structure.state import State
from protein_information_system.sql.model.entities.structure.structure import Structure

@pytest.mark.order(5)
class TestStructure3DiManager(unittest.TestCase):

    def setUp(self):
        config_path = os.path.join('protein_information_system/config/', "config.yaml")
        config = read_yaml_config(config_path)
        constants_path = os.path.join('protein_information_system/config/', "constants.yaml")
        config['constants'] = constants_path
        self.embedder = Structure3DiManager(config)

    def get_config_path(self):
        """Devuelve la ruta al archivo de configuración."""
        return os.path.join('tests/config/', "config.yaml")  # Ruta del archivo YAML de prueba

    def test_entities_created(self):
        """Verifica que se crean instancias en todas las entidades tras ejecutar el embedder."""
        # Ejecutar el manager para procesar embeddings
        self.embedder.start()

        embeddings = self.embedder.session.query(Structure3Di).all()
        self.assertGreater(len(embeddings), 0, "No se han creado embeddings 3Di.")

        for embedding in embeddings:
            # Comprobar que cada embedding tiene datos válidos
            self.assertIsNotNone(embedding.embedding, "El embedding está vacío.")
            self.assertIsNotNone(embedding.state_id, "El embedding no tiene un state_id asociado.")

            # Verificar que el estado asociado existe en la base de datos
            state = self.embedder.session.query(State).filter_by(id=embedding.state_id).first()
            self.assertIsNotNone(state, f"No se encontró un estado asociado para state_id={embedding.state_id}.")

        # Cerrar la sesión al finalizar
        self.embedder.session.close()



    if __name__ == '__main__':
        unittest.main()