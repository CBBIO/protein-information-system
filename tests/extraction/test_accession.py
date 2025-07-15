import re
import unittest
import os

import pytest

from protein_information_system.helpers.config.yaml import read_yaml_config
from protein_information_system.operation.extraction.accessions import AccessionManager
from protein_information_system.sql.model.entities.protein.accesion import Accession

@pytest.mark.order(1)
class TestAccessionManager(unittest.TestCase):

    def setUp(self):
        config_path = os.path.join('protein_information_system/config/', "config.yaml")
        config = read_yaml_config(config_path)
        constants_path = os.path.join('protein_information_system/config/', "constants.yaml")
        config['constants'] = constants_path
        self.extractor = AccessionManager(config)



    def test_conf(self):
        """Verifica que la configuración se haya cargado correctamente."""
        self.assertIn("DB_USERNAME", self.extractor.conf)  # Ajusta según tu YAML
        self.assertIn("DB_HOST", self.extractor.conf)  # Ajusta según tu YAML
        self.assertIn("DB_NAME", self.extractor.conf)  # Ajusta según tu YAML

    def test_api_results(self):
        """Verifica que se carguen accesiones desde la API."""
        self.extractor.fetch_accessions_from_api()

        # Verificamos que se hayan cargado accesiones
        accessions = self.extractor.session.query(Accession).all()
        self.assertGreater(len(accessions), 0, "No se cargaron accesiones desde la API")

    def test_csv_results(self):
        """Verifica que se carguen accesiones desde la API."""
        self.extractor.load_accessions_from_csv()

    def test_unique_accessions(self):
        """Verifica que las accesiones cargadas sean únicas."""
        accessions = self.extractor.session.query(Accession).all()
        accession_codes = [acc.code for acc in accessions]

        # Verificamos que no haya accesiones duplicadas
        self.assertEqual(len(accession_codes), len(set(accession_codes)), "Hay accesiones duplicadas en los resultados")


    def test_valid_accession_format(self):
        """Verifica que las accesiones tengan un formato válido (de 6 a 10 caracteres alfanuméricos)."""
        self.extractor.fetch_accessions_from_api()

        accessions = self.extractor.session.query(Accession).all()

        # Definimos un patrón para los códigos de acceso: 6 a 10 caracteres alfanuméricos
        accession_pattern = re.compile(r'^[A-Za-z0-9]{6,10}$')

        for accession in accessions:
            self.assertTrue(accession_pattern.match(accession.code),
                            f"Formato inválido para la accesión {accession.code}")


