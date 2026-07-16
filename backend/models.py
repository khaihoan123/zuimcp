from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text
from database import Base

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    google_access_token: Mapped[str] = mapped_column(Text)
    google_refresh_token: Mapped[str] = mapped_column(Text)