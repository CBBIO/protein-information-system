import logging

from protein_information_system.helpers.services.services import check_services


def main(config_path='config/config.yaml'):
    from protein_information_system.helpers.config.yaml import read_yaml_config
    conf = read_yaml_config(config_path)

    logger = logging.getLogger("protein_information_system")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    # Step 1: Import ORM-based logic & check model coherence
    from protein_information_system.sql.model.model import (
        AccessionManager,
        UniProtExtractor,
        PDBExtractor,
        SequenceEmbeddingManager,
        Structure3DiManager
    )

    # Step 2: Check services running

    check_services(conf, logger)

    # Step 3: Run components
    # AccessionManager(conf).fetch_accessions_from_api()
    # UniProtExtractor(conf).start()
    # PDBExtractor(conf).start()
    SequenceEmbeddingManager(conf).start()
    # Structure3DiManager(conf).start()


if __name__ == '__main__':
    main()
