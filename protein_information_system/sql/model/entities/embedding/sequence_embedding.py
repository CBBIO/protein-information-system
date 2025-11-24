from pgvector.sqlalchemy import HALFVEC
from sqlalchemy.orm import relationship, mapped_column
from sqlalchemy import (
    Column, Integer, String, ForeignKey, ARRAY, DateTime, func,
    UniqueConstraint, Index
)

from protein_information_system.sql.model.core.base import Base


class SequenceEmbeddingType(Base):
    """
    Represents a type of protein sequence analysis embedding.

    This class is designed to manage different embedding techniques used in protein sequence analysis,
    offering a structured way to categorize and store information about various embedding methods such as ESM and Prot-T5.

    Attributes:
        id (Integer): Unique identifier for each embedding type.
        name (String): Unique name of the embedding type.
        description (String): Detailed description of the embedding technique.
        task_name (String): Name of the specific task associated with this embedding type, if applicable.
    """
    __tablename__ = 'sequence_embedding_type'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    description = Column(String)
    task_name = Column(String)
    model_name = Column(String)

    seq_embeddings = relationship("SequenceEmbedding", back_populates="embedding_type")


class SequenceEmbedding(Base):
    __tablename__ = 'sequence_embeddings'

    id = Column(Integer, primary_key=True)
    sequence_id = Column(Integer, ForeignKey('sequence.id'), nullable=False)
    embedding_type_id = Column(Integer, ForeignKey('sequence_embedding_type.id'), nullable=False)
    layer_index = Column(Integer, nullable=False)
    embedding = mapped_column(HALFVEC(), nullable=False)
    shape = Column(ARRAY(Integer))
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    sequence = relationship("Sequence")
    embedding_type = relationship("SequenceEmbeddingType")

    __table_args__ = (
        UniqueConstraint('sequence_id', 'embedding_type_id', 'layer_index',
                         name='uq_seqemb_seq_type_layer'),
        Index('ix_seqemb_seq', 'sequence_id'),
        Index('ix_seqemb_type', 'embedding_type_id'),
    )
