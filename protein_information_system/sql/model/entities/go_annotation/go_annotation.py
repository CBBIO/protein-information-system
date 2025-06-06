from sqlalchemy import String, ForeignKey, Column, Integer
from sqlalchemy.orm import relationship
from protein_information_system.sql.model.core.base import Base


class ProteinGOTermAnnotation(Base):
    __tablename__ = 'protein_go_term_annotation'
    id = Column(Integer, primary_key=True, autoincrement=True)
    protein_id = Column(String, ForeignKey('protein.id'), primary_key=True)
    go_id = Column(String, ForeignKey('go_terms.go_id'), primary_key=True)
    evidence_code = Column(String, nullable=False)  # Added field for evidence

    protein = relationship("Protein", back_populates="annotations")
    go_term = relationship("GOTerm", back_populates="annotations")

    def __repr__(self):
        return f"<SequenceGOTermAnnotation(protein_id={self.protein_id}, go_id={self.go_id}, evidence_code={self.evidence_code})>"
