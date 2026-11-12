from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from repopilot_api.database import Base


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    owner: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    full_name: Mapped[str] = mapped_column(String(201), unique=True, index=True)
    url: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    default_branch: Mapped[str] = mapped_column(String(255))
    stars: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IndexingJob(Base):
    __tablename__ = "indexing_jobs"
    __table_args__ = (
        UniqueConstraint("repository_id", "commit_sha", name="uq_index_job_repo_sha"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    commit_sha: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IndexedFile(Base):
    __tablename__ = "indexed_files"
    __table_args__ = (UniqueConstraint("job_id", "path", name="uq_indexed_file_job_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("indexing_jobs.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(1024), index=True)
    language: Mapped[str] = mapped_column(String(40), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)


class CodeSymbol(Base):
    __tablename__ = "code_symbols"
    __table_args__ = (
        UniqueConstraint("file_id", "kind", "name", "line", name="uq_symbol_file_kind_name_line"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("indexed_files.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    line: Mapped[int] = mapped_column(Integer)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    commit_sha: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
