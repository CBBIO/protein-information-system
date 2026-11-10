from sqlalchemy import create_engine, QueuePool, text
from sqlalchemy.orm import sessionmaker
from protein_information_system.sql.model.core.base import Base

import logging


def ensure_pgvector_extension(engine):
    logger = logging.getLogger("protein_information_system")
    logger.setLevel(logging.INFO)
    try:
        with engine.begin() as conn:
            logger.info("Ensuring pgvector extension is enabled...")
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            logger.info("pgvector extension verified or created successfully.")
    except Exception as e:
        logger.error(f"Could not create pgvector extension: {e}")


class DatabaseManager:
    def __init__(self, conf):
        self.conf = conf
        self.engine = self.create_engine()
        self.Session = sessionmaker(bind=self.engine)

    def create_engine(self):
        """
        Create the SQLAlchemy engine with a QueuePool for connection management.
        Ensures the pgvector extension is enabled before creating any tables.
        """
        DATABASE_URI = (
            f"postgresql+psycopg2://{self.conf['DB_USERNAME']}:"
            f"{self.conf['DB_PASSWORD']}"
            f"@{self.conf['DB_HOST']}:{self.conf['DB_PORT']}/"
            f"{self.conf['DB_NAME']}"
        )

        engine = create_engine(
            DATABASE_URI,
            pool_size=0,
            max_overflow=0,
            poolclass=QueuePool,
            pool_pre_ping=True,
            # Always talk UTF-8. A cluster created under LC_ALL=C is SQL_ASCII, and
            # psycopg2 would otherwise decode every text column as ASCII.
            connect_args={"client_encoding": "utf8"},
        )

        ensure_pgvector_extension(engine)
        Base.metadata.create_all(engine)

        return engine

    def get_session(self):
        return self.Session()

    def get_engine(self):
        return self.engine

    def get_pool(self):
        return self.engine.pool
