"""merge the develop and account-storage migration heads"""

from alembic import op

revision = "20260927_45_merge_storage_heads"
down_revision = (
    "20260920_58_knowledge_book_files",
    "20260927_44_storage_pool_lock",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    comments = {
        "nlp_user_files": "用户独立文件夹中的个人文件与目录索引。",
        "nlp_storage_accounts": "按账户聚合的通用/个人文件用量、预留量与管理员配额覆盖。",
        "nlp_storage_reservations": "写入前的存储容量预留及提交/释放审计记录。",
        "nlp_storage_quota_audits": "管理员调整账户存储配额的不可变审计记录。",
    }
    for table_name, comment in comments.items():
        op.execute(f"ALTER TABLE `{table_name}` COMMENT = '{comment}'")


def downgrade() -> None:
    pass
