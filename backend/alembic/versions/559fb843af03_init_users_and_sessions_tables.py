"""init users and sessions tables

Revision ID: 559fb843af03
Revises: 
Create Date: 2026-05-20 20:15:37.226018

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from app.db import Base


# revision identifiers, used by Alembic.
revision: str = '559fb843af03'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: create all tables from SQLAlchemy models."""
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = inspector.get_table_names()
    
    # Only create tables that don't already exist
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            table.create(bind)


def downgrade() -> None:
    """Downgrade schema: drop all tables."""
    bind = op.get_bind()
    inspector = inspect(bind)
    existing_tables = inspector.get_table_names()
    
    # Drop tables in reverse dependency order
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in existing_tables:
            table.drop(bind)
