import os
import unittest

import pytest
from sqlalchemy.dialects.mssql.information_schema import sequences

from protein_information_system.helpers.config.yaml import read_yaml_config
from protein_information_system.operation.embedding.sequence_embedding import SequenceEmbeddingManager
from protein_information_system.operation.extraction.uniprot import UniProtExtractor
from protein_information_system.sql.model.entities.embedding.sequence_embedding import SequenceEmbedding, \
    SequenceEmbeddingType
from protein_information_system.sql.model.entities.go_annotation.go_annotation import ProteinGOTermAnnotation
from protein_information_system.sql.model.entities.go_annotation.go_term import GOTerm
from protein_information_system.sql.model.entities.protein.accesion import Accession
from protein_information_system.sql.model.entities.protein.protein import Protein
from protein_information_system.sql.model.entities.sequence.sequence import Sequence
from protein_information_system.sql.model.entities.structure.structure import Structure

@pytest.mark.order(4)
class TestSequenceEmbeddingManager(unittest.TestCase):

    def setUp(self):
        config_path = os.path.join('protein_information_system/config/', "config.yaml")
        config = read_yaml_config(config_path)
        constants_path = os.path.join('protein_information_system/config/', "constants.yaml")
        config['constants'] = constants_path
        self.embedder = SequenceEmbeddingManager(config)

    def get_config_path(self):
        """Devuelve la ruta al archivo de configuración."""
        return os.path.join('tests/config/', "config.yaml")  # Ruta del archivo YAML de prueba

    def test_entities_created(self):
        """Verifica que se crean instancias en todas las entidades tras ejecutar el embedder."""
        # Ejecutar el manager para procesar embeddings
        self.embedder.start()

        # Verificar que se han creado embeddings en la base de datos
        self.embedder.session_init()

        # Verificar que se han creado tipos de embedding
        embedding_types = self.embedder.session.query(SequenceEmbeddingType).all()
        self.assertGreater(len(embedding_types), 0, "No se han creado tipos de embedding.")

        # Verificar que se han creado embeddings
        embeddings = self.embedder.session.query(SequenceEmbedding).all()
        self.assertGreater(len(embeddings), 0, "No se han creado embeddings.")

        # Verificar que los embeddings tienen datos válidos
        for embedding in embeddings:
            self.assertIsNotNone(embedding.embedding, "El embedding no tiene datos.")
            self.assertIsNotNone(embedding.shape, "El embedding no tiene una forma definida.")
            self.assertGreater(len(embedding.shape), 0, "La forma del embedding está vacía.")

        # Cerrar la sesión después de las verificaciones
        self.embedder.session.close()



    if __name__ == '__main__':
        unittest.main()