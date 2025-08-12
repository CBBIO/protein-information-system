from sqlalchemy import Column, String
from sqlalchemy.orm import relationship
from protein_information_system.sql.model.core.base import Base


class GOTerm(Base):
    __tablename__ = "go_terms"

    go_id = Column(String, primary_key=True, nullable=False)
    category = Column(String, nullable=False, default="undefined")
    description = Column(String)

    # Relaciones
    annotations = relationship("ProteinGOTermAnnotation", back_populates="go_term")
    # entries = relationship("GOTermPairEntry", back_populates="go_term")

    def __repr__(self):
        return f"<GOTerm(go_id={self.go_id}, category={self.category}, description={self.description})>"
