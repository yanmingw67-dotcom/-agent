"""P1.3整改：来源分类默认企业，历史素材不自动降级。"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("source_snapshots", "documents"):
        op.add_column(
            table,
            sa.Column(
                "data_classification", sa.String(32), nullable=False, server_default="enterprise"
            ),
        )


def downgrade():
    for table in ("documents", "source_snapshots"):
        op.drop_column(table, "data_classification")
