"""班级加入申请模块（V3 适配版）。

仅实现「学生提交加入申请 → 教师审批/拒绝」工作流，复用 V3 既有的
``nlp_classrooms`` / ``nlp_classroom_members`` 与 RBAC 权限体系，不引入第二套
班级系统，也不重复实现类 CRUD / 成员管理（由 ``server/web/app.py`` 的
``/api/v1/classrooms`` 提供）。对应 review 文档阶段4「班级工作流」的接入部分，
提前到阶段1 落地以满足原始计划，同时遵守 P0-3 / P0-5 / 第4.1 / 第6.2。
"""

from __future__ import annotations

from server.classroom_join.api import router

__all__ = ["router"]
