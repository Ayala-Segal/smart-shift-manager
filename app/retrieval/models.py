from app.extensions import db


class EmbeddingProfile(db.Model):
    """Source text for an employee profile. Vectors live in the vector store."""

    __tablename__ = "embedding_profiles"

    employee_id = db.Column(db.String(64), primary_key=True)
    profile_text = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False)
