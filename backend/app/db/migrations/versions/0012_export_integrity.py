"""P2.3 导出下载凭证有效期与文件完整性摘要。

旧导出任务不自动回填：
- download_expires_at 为空表示凭证已不可用，必须由所属用户重新签发；
- file_sha256 为空表示历史文件缺少生成时摘要，不能证明文件未被替换，
  下载时失败关闭并要求重新生成导出。
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "evaluation_export_jobs",
        sa.Column("download_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "evaluation_export_jobs",
        sa.Column("file_sha256", sa.String(64), nullable=True),
    )


def downgrade():
    op.drop_column("evaluation_export_jobs", "file_sha256")
    op.drop_column("evaluation_export_jobs", "download_expires_at")
