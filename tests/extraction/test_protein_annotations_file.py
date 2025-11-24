import os
import unittest

import pytest

from protein_information_system.helpers.config.yaml import read_yaml_config
from protein_information_system.operation.extraction.protein_annotations_file import GOAnnotationsQueueProcessor
from protein_information_system.sql.model.entities.go_annotation.go_term import GOTerm
from protein_information_system.sql.model.entities.go_annotation.go_annotation import ProteinGOTermAnnotation
from protein_information_system.sql.model.entities.protein.protein import Protein
from protein_information_system.sql.model.entities.sequence.sequence import Sequence

def _write_temp_fasta(dirpath):
    path = os.path.join(dirpath, "sequences.fasta")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            ">sp|P12345|SAMPLE_PROT_A Some desc\n"
            "MKTAYIAKQRQISFVKSHFSRQ\n"
            ">tr|Q8XYZ1|SAMPLE_PROT_B Other desc\n"
            "GAVLILKKKQQQPPTTA\n"
        )
    return path

def _write_temp_cafa(dirpath):
    path = os.path.join(dirpath, "goa.tsv")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "# comment\n"
            "UniProtKB:P12345\tGO:0008150\tBP\n"
            "UniProtKB:Q8XYZ1\tGO:0005575\tCC\n"
        )
    return path


@pytest.mark.order(2)
class TestGOAnnotationsQueueProcessor(unittest.TestCase):

    def setUp(self):
        # 1) Cargar config real como en el resto de tests
        config_path = os.path.join('protein_information_system', 'config', 'config.yaml')
        config = read_yaml_config(config_path)
        constants_path = os.path.join('protein_information_system', 'config', 'constants.yaml')
        config['constants'] = constants_path
        config['limit_execution'] = 5  # igual que el resto

        # 2) Crear insumos mínimos de test (sin cambiar el flujo ni la clase)
        tmp_dir = os.path.abspath(os.path.join(os.getcwd(), ".tmp_goa_tests"))
        os.makedirs(tmp_dir, exist_ok=True)
        config['goa_sequences_fasta'] = _write_temp_fasta(tmp_dir)
        config['goa_annotations_file'] = _write_temp_cafa(tmp_dir)

        # 3) Instancia real (sin monkey patch)
        self.extractor = GOAnnotationsQueueProcessor(config)

    def test_conf(self):
        """Verifica que la configuración se haya cargado correctamente."""
        self.assertIn("DB_USERNAME", self.extractor.conf)
        self.assertIn("DB_HOST", self.extractor.conf)
        self.assertIn("DB_NAME", self.extractor.conf)

    def test_start_creates_entities(self):
        """Ejecuta el proceso y verifica inserciones mínimas."""
        # Igual que en uniprot/pdb: ejecutamos el flujo principal
        self.extractor.start()

        # Verificar que hay resultados básicos en BD
        go_terms = self.extractor.session.query(GOTerm).all()
        self.assertGreater(len(go_terms), 0, "No se crearon términos GO.")

        go_ann = self.extractor.session.query(ProteinGOTermAnnotation).all()
        self.assertGreater(len(go_ann), 0, "No se crearon anotaciones GO.")

        proteins = self.extractor.session.query(Protein).all()
        self.assertGreater(len(proteins), 0, "No se crearon proteínas.")

        sequences = self.extractor.session.query(Sequence).all()
        self.assertGreater(len(sequences), 0, "No se crearon secuencias.")

    def test_enqueue_runs(self):
        """Verifica que enqueue() se ejecute sin errores (estilo smoke test)."""
        self.extractor.enqueue()
