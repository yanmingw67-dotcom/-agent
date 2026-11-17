"""区分 outbox 的已提交派发租约与 Redis 投递确认。"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("knowledge_jobs", sa.Column("dispatch_enqueued_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("knowledge_jobs", "dispatch_enqueued_at")
